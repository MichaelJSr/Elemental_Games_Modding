#!/usr/bin/env python3
"""Extract Azurik save data from xemu QCOW2 HDD image."""
import struct
import os
import sys

QCOW2_PATH = "xbox_hdd.qcow2"
OUTPUT_DIR = "save_data"
TITLE_ID = "4d530007"

# Xbox HDD E: partition starts at this offset
E_PARTITION_OFFSET = 0xABE80000

# FATX constants
FATX_MAGIC = b'FATX'
FATX_CLUSTER_SIZE_SECTORS = None  # read from superblock
SECTOR_SIZE = 512

class QCOW2Reader:
    """Minimal QCOW2 reader that translates virtual offsets to host offsets."""

    def __init__(self, path):
        self.f = open(path, 'rb')
        self._parse_header()
        self._load_l1_table()

    def _parse_header(self):
        self.f.seek(0)
        hdr = self.f.read(104)
        magic = hdr[0:4]
        if magic != b'QFI\xfb':
            raise ValueError(f"Not a QCOW2 file: {magic}")
        self.version = struct.unpack('>I', hdr[4:8])[0]
        self.backing_file_offset = struct.unpack('>Q', hdr[8:16])[0]
        self.backing_file_size = struct.unpack('>I', hdr[16:20])[0]
        self.cluster_bits = struct.unpack('>I', hdr[20:24])[0]
        self.cluster_size = 1 << self.cluster_bits
        self.disk_size = struct.unpack('>Q', hdr[24:32])[0]
        self.crypt_method = struct.unpack('>I', hdr[32:36])[0]
        self.l1_size = struct.unpack('>I', hdr[36:40])[0]
        self.l1_table_offset = struct.unpack('>Q', hdr[40:48])[0]
        self.refcount_table_offset = struct.unpack('>Q', hdr[48:56])[0]
        self.refcount_table_clusters = struct.unpack('>I', hdr[56:60])[0]
        self.nb_snapshots = struct.unpack('>I', hdr[60:64])[0]
        self.snapshots_offset = struct.unpack('>Q', hdr[64:72])[0]

        # L2 entries per table
        self.l2_entries = self.cluster_size // 8

    def _load_l1_table(self):
        self.f.seek(self.l1_table_offset)
        raw = self.f.read(self.l1_size * 8)
        self.l1_table = struct.unpack(f'>{self.l1_size}Q', raw)

    def read(self, offset, size):
        """Read `size` bytes from virtual disk offset."""
        result = bytearray()
        while len(result) < size:
            pos = offset + len(result)
            remaining = size - len(result)

            # Which cluster?
            cluster_idx = pos >> self.cluster_bits
            in_cluster_offset = pos & (self.cluster_size - 1)
            can_read = min(remaining, self.cluster_size - in_cluster_offset)

            # L1 index and L2 index
            l2_bits = self.cluster_bits - 3  # log2(l2_entries)
            l1_idx = cluster_idx >> l2_bits
            l2_idx = cluster_idx & ((1 << l2_bits) - 1)

            if l1_idx >= self.l1_size:
                result.extend(b'\x00' * can_read)
                continue

            l1_entry = self.l1_table[l1_idx]
            l2_offset = l1_entry & 0x00fffffffffffe00

            if l2_offset == 0:
                result.extend(b'\x00' * can_read)
                continue

            # Read L2 entry
            self.f.seek(l2_offset + l2_idx * 8)
            l2_entry = struct.unpack('>Q', self.f.read(8))[0]

            host_cluster_offset = l2_entry & 0x00fffffffffffe00
            compressed = (l2_entry >> 62) & 1

            if host_cluster_offset == 0:
                result.extend(b'\x00' * can_read)
                continue

            if compressed:
                raise NotImplementedError("Compressed clusters not supported")

            # Read from host
            self.f.seek(host_cluster_offset + in_cluster_offset)
            data = self.f.read(can_read)
            result.extend(data)

        return bytes(result)

    def close(self):
        self.f.close()


class FATXReader:
    """Minimal FATX filesystem reader."""

    def __init__(self, qcow2, partition_offset):
        self.qcow2 = qcow2
        self.part_offset = partition_offset
        self._parse_superblock()

    def _parse_superblock(self):
        sb = self.qcow2.read(self.part_offset, 4096)
        magic = sb[0:4]
        if magic != FATX_MAGIC:
            raise ValueError(f"Not FATX at offset 0x{self.part_offset:X}: {magic}")

        self.volume_id = struct.unpack('<I', sb[4:8])[0]
        self.sectors_per_cluster = struct.unpack('<I', sb[8:12])[0]
        self.num_fat_copies = struct.unpack('<H', sb[12:14])[0]

        self.cluster_size = self.sectors_per_cluster * SECTOR_SIZE

        # FAT starts at offset 0x1000 (4096) from partition start
        self.fat_offset = self.part_offset + 0x1000

        # Calculate partition size to determine FAT entry size
        # For E: partition, it's large enough to need 32-bit FAT entries
        self.fat_entry_size = 4  # 32-bit for large partitions

        # Root directory cluster
        self.root_cluster = 1  # FATX root is always cluster 1

        # Calculate data area start
        # FAT size depends on number of clusters
        # For simplicity, calculate from known Xbox layout
        # The data area starts after the FAT
        # Max clusters for E: ~ 0x1312D6000 / cluster_size
        total_bytes = 0x1312D6000  # E: partition size
        self.max_clusters = total_bytes // self.cluster_size
        fat_size = self.max_clusters * self.fat_entry_size
        # FAT is aligned to page boundary
        fat_pages = (fat_size + 4095) // 4096
        self.data_offset = self.fat_offset + fat_pages * 4096

        print(f"FATX: cluster_size={self.cluster_size}, max_clusters={self.max_clusters}")
        print(f"  FAT at 0x{self.fat_offset:X}, data at 0x{self.data_offset:X}")

    def _cluster_to_offset(self, cluster):
        return self.data_offset + (cluster - 1) * self.cluster_size

    def _read_fat_entry(self, cluster):
        fat_pos = self.fat_offset + cluster * self.fat_entry_size
        data = self.qcow2.read(fat_pos, self.fat_entry_size)
        if self.fat_entry_size == 4:
            return struct.unpack('<I', data)[0]
        else:
            return struct.unpack('<H', data)[0]

    def _read_cluster_chain(self, start_cluster, max_size=None):
        """Read all clusters in a chain."""
        data = bytearray()
        cluster = start_cluster
        while True:
            if cluster == 0 or cluster >= 0xFFFFFFF0:
                break
            offset = self._cluster_to_offset(cluster)
            chunk = self.qcow2.read(offset, self.cluster_size)
            data.extend(chunk)
            if max_size and len(data) >= max_size:
                break
            cluster = self._read_fat_entry(cluster)
        if max_size:
            data = data[:max_size]
        return bytes(data)

    def _parse_directory(self, cluster):
        """Parse directory entries from a cluster chain."""
        dir_data = self._read_cluster_chain(cluster)
        entries = []
        i = 0
        while i + 64 <= len(dir_data):
            entry_data = dir_data[i:i+64]
            name_len = entry_data[0]

            if name_len == 0xFF or name_len == 0x00:
                i += 64
                continue

            if name_len == 0xE5:  # deleted
                i += 64
                continue

            attrs = entry_data[1]
            name_raw = entry_data[2:2+min(name_len, 42)]
            name = name_raw.decode('ascii', errors='replace').rstrip('\x00')

            first_cluster = struct.unpack('<I', entry_data[44:48])[0]
            file_size = struct.unpack('<I', entry_data[48:52])[0]

            # Timestamps
            mod_time = struct.unpack('<H', entry_data[52:54])[0]
            mod_date = struct.unpack('<H', entry_data[54:56])[0]
            create_time = struct.unpack('<H', entry_data[56:58])[0]
            create_date = struct.unpack('<H', entry_data[58:60])[0]
            access_time = struct.unpack('<H', entry_data[60:62])[0]
            access_date = struct.unpack('<H', entry_data[62:64])[0]

            is_dir = bool(attrs & 0x10)

            entries.append({
                'name': name,
                'is_dir': is_dir,
                'cluster': first_cluster,
                'size': file_size,
                'attrs': attrs,
            })

            i += 64

        return entries

    def list_dir(self, path):
        """List directory at given path."""
        parts = [p for p in path.strip('/').split('/') if p]
        cluster = self.root_cluster

        for part in parts:
            entries = self._parse_directory(cluster)
            found = False
            for e in entries:
                if e['name'].lower() == part.lower() and e['is_dir']:
                    cluster = e['cluster']
                    found = True
                    break
            if not found:
                raise FileNotFoundError(f"Directory not found: {part} in {path}")

        return self._parse_directory(cluster)

    def read_file(self, path):
        """Read a file at given path."""
        parts = [p for p in path.strip('/').split('/') if p]
        filename = parts[-1]
        dir_parts = parts[:-1]

        cluster = self.root_cluster
        for part in dir_parts:
            entries = self._parse_directory(cluster)
            found = False
            for e in entries:
                if e['name'].lower() == part.lower() and e['is_dir']:
                    cluster = e['cluster']
                    found = True
                    break
            if not found:
                raise FileNotFoundError(f"Dir not found: {part}")

        entries = self._parse_directory(cluster)
        for e in entries:
            if e['name'].lower() == filename.lower() and not e['is_dir']:
                return self._read_cluster_chain(e['cluster'], e['size'])

        raise FileNotFoundError(f"File not found: {filename}")


def main():
    print(f"Opening {QCOW2_PATH}...")
    qcow2 = QCOW2Reader(QCOW2_PATH)
    print(f"  QCOW2 v{qcow2.version}, disk_size={qcow2.disk_size}, cluster_size={qcow2.cluster_size}")

    print(f"\nReading E: partition at offset 0x{E_PARTITION_OFFSET:X}...")
    fatx = FATXReader(qcow2, E_PARTITION_OFFSET)

    # List root
    print("\nE:\\ root:")
    for e in fatx.list_dir('/'):
        typ = 'DIR' if e['is_dir'] else 'FILE'
        print(f"  [{typ}] {e['name']} (cluster={e['cluster']}, size={e['size']})")

    # Find Azurik save
    save_path = f"UDATA/{TITLE_ID}"
    print(f"\nListing {save_path}/:")
    try:
        entries = fatx.list_dir(save_path)
        for e in entries:
            typ = 'DIR' if e['is_dir'] else 'FILE'
            print(f"  [{typ}] {e['name']} (size={e['size']})")

        # Find the slot directory
        slot_dirs = [e for e in entries if e['is_dir'] and e['name'] not in ['.', '..']]

        for slot in slot_dirs:
            slot_path = f"{save_path}/{slot['name']}"
            print(f"\nListing {slot_path}/:")
            slot_entries = fatx.list_dir(slot_path)
            for e in slot_entries:
                typ = 'DIR' if e['is_dir'] else 'FILE'
                print(f"  [{typ}] {e['name']} (size={e['size']})")

            # Extract save files
            os.makedirs(f"{OUTPUT_DIR}/{slot['name']}", exist_ok=True)
            for e in slot_entries:
                if not e['is_dir'] and e['size'] > 0:
                    fpath = f"{slot_path}/{e['name']}"
                    outpath = f"{OUTPUT_DIR}/{slot['name']}/{e['name']}"
                    print(f"  Extracting {e['name']} ({e['size']} bytes)...")
                    data = fatx.read_file(fpath)
                    with open(outpath, 'wb') as out:
                        out.write(data)
                    print(f"    -> {outpath}")

            # Check for levels subdirectory
            level_dirs = [e for e in slot_entries if e['is_dir'] and e['name'].lower() == 'levels']
            for ld in level_dirs:
                ld_path = f"{slot_path}/levels"
                print(f"\nListing {ld_path}/:")
                try:
                    level_entries = fatx.list_dir(ld_path)
                    os.makedirs(f"{OUTPUT_DIR}/{slot['name']}/levels", exist_ok=True)
                    for e in level_entries:
                        if e['is_dir']:
                            print(f"  [DIR] {e['name']}")
                        else:
                            print(f"  [FILE] {e['name']} ({e['size']} bytes)")
                            fpath = f"{ld_path}/{e['name']}"
                            outpath = f"{OUTPUT_DIR}/{slot['name']}/levels/{e['name']}"
                            data = fatx.read_file(fpath)
                            with open(outpath, 'wb') as out:
                                out.write(data)
                except Exception as ex:
                    print(f"  Error: {ex}")

    except FileNotFoundError as ex:
        print(f"  Not found: {ex}")

    qcow2.close()
    print("\nDone!")


if __name__ == '__main__':
    main()
