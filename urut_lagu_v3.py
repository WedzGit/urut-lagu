import os
import sys
import json
import shutil
import string
import ctypes
import hashlib
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
import tkinter as tk
from tkinter import filedialog

APP_NAME = "Urut Lagu v3.0"
MANIFEST_NAME = ".urut_lagu.json"
AUDIO_EXTENSIONS = {".mp3"}
DRIVE_REMOVABLE = 2


@contextmanager
def processing(message="Memproses"):
    """Animasi spinner saat proses file berjalan."""
    stop_event = threading.Event()

    def animate():
        frames = ["|", "/", "-", "\\"]
        i = 0

        while not stop_event.is_set():
            print(
                f"\r{message} {frames[i % len(frames)]}",
                end="",
                flush=True
            )
            i += 1
            stop_event.wait(0.12)

        print(f"\r{message} selesai.     ", flush=True)

    worker = threading.Thread(
        target=animate,
        daemon=True
    )
    worker.start()

    try:
        yield
    finally:
        stop_event.set()
        worker.join()
        print()


def pause():
    input("\nTekan Enter untuk keluar...")


def get_drive_type(path):
    return ctypes.windll.kernel32.GetDriveTypeW(str(path))


def get_volume_label(path):
    label = ctypes.create_unicode_buffer(261)
    result = ctypes.windll.kernel32.GetVolumeInformationW(
        str(path), label, len(label), None, None, None, None, 0
    )
    return label.value if result else "(Tanpa nama)"


def find_removable_drives():
    drives = []
    for letter in string.ascii_uppercase:
        path = Path(f"{letter}:/")
        if path.exists():
            try:
                if get_drive_type(path) == DRIVE_REMOVABLE:
                    drives.append({"path": path, "label": get_volume_label(path)})
            except Exception:
                pass
    return drives


def choose_usb():
    drives = find_removable_drives()
    print("\n=== PILIH FLASHDISK ===")
    if not drives:
        print("Tidak ditemukan drive removable. Pastikan flashdisk terpasang.")
        return None
    for i, d in enumerate(drives, 1):
        print(f"[{i}] {d['label']} ({d['path'].drive})")
    print("[0] Batal")
    while True:
        choice = input("Pilih flashdisk: ").strip()
        if choice == "0":
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(drives):
            d = drives[int(choice) - 1]
            if d["path"].exists() and get_drive_type(d["path"]) == DRIVE_REMOVABLE:
                return d
        print("Pilihan tidak valid.")


def choose_folders(usb):
    folders = sorted(
        [p for p in usb.iterdir() if p.is_dir() and not p.name.startswith("__URUT_")],
        key=lambda p: p.name.casefold()
    )
    if not folders:
        print("Tidak ditemukan folder musik di root flashdisk.")
        return []
    print("\n=== FOLDER DI FLASHDISK ===")
    for i, folder in enumerate(folders, 1):
        count = len(audio_files(folder))
        print(f"[{i}] {folder.name} ({count} MP3)")
    print("[A] Semua folder")
    print("[0] Batal")
    while True:
        choice = input("Pilih nomor (mis. 1,3) atau A: ").strip().upper()
        if choice == "0":
            return []
        if choice == "A":
            return folders
        try:
            nums = [int(x.strip()) for x in choice.split(",")]
            if not nums or any(n < 1 or n > len(folders) for n in nums):
                raise ValueError
            return list(dict.fromkeys(folders[n - 1] for n in nums))
        except ValueError:
            print("Pilihan tidak valid.")


def choose_temp_root(usb):
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    selected = filedialog.askdirectory(title="Pilih lokasi sementara di SSD/HDD komputer")
    root.destroy()
    if not selected:
        return None
    path = Path(selected).resolve()
    if path.drive.lower() == usb.drive.lower():
        print("Pilih lokasi di SSD/HDD komputer, bukan di flashdisk.")
        return None
    return path


def load_manifest(usb):
    path = usb / MANIFEST_NAME
    if not path.exists():
        print("Manifest belum ada; akan dibuat setelah proses berhasil.")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Struktur manifest bukan objek JSON.")
        return data
    except Exception as e:
        raise RuntimeError(f"Manifest gagal dibaca. Tidak diubah: {e}")


def save_manifest_atomic(usb, manifest):
    path = usb / MANIFEST_NAME
    temp_path = usb / (MANIFEST_NAME + ".tmp")
    temp_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    os.replace(temp_path, path)


def clean_name(filename):
    name = filename
    if " - " in name:
        prefix, rest = name.split(" - ", 1)
        if prefix.isdigit():
            name = rest
    return name


def get_key(filename):
    return clean_name(filename).casefold()


def audio_files(folder):
    return [
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
    ]


def ordered_files(folder, manifest):
    files = audio_files(folder)
    current = {}
    for file in files:
        key = get_key(file.name)
        if key in current:
            raise RuntimeError(
                f"Nama lagu bentrok setelah nomor dihapus di {folder.name}: {file.name}"
            )
        current[key] = file

    saved = manifest.get(folder.name, [])
    if not isinstance(saved, list):
        raise RuntimeError(f"Data manifest untuk {folder.name} tidak valid.")

    keys = [key for key in saved if key in current]
    known = set(keys)
    new_count = 0
    for key, file in current.items():
        if key not in known:
            print(f"  Lagu baru: {file.name}")
            keys.append(key)
            known.add(key)
            new_count += 1

    manifest[folder.name] = keys
    return [current[key] for key in keys], new_count


def confirm(message):
    while True:
        answer = input(f"\n{message} (Y/N): ").strip().upper()
        if answer == "Y":
            return True
        if answer == "N":
            return False
        print("Masukkan Y atau N.")


def hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_temp(folders, manifest, temp_root):
    run_dir = temp_root / (
        "UrutLagu_" + __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    details = {}
    try:
        for folder in folders:
            ordered, new_count = ordered_files(folder, manifest)
            target = run_dir / folder.name
            target.mkdir()
            print(f"\nMenyiapkan {folder.name}: {len(ordered)} lagu ({new_count} baru)")
            records = []

            for index, src in enumerate(ordered, 1):
                dest_name = f"{index:04d} - {clean_name(src.name)}"
                dest = target / dest_name

                with processing(
                    f"Menyalin {index}/{len(ordered)}: {src.name[:40]}"
                ):
                    shutil.copy2(src, dest)

                    if (
                        src.stat().st_size != dest.stat().st_size
                        or hash_file(src) != hash_file(dest)
                    ):
                        raise RuntimeError(f"Verifikasi gagal: {src.name}")

                    file_hash = hash_file(dest)

                records.append({
                    "name": dest_name,
                    "key": get_key(src.name),
                    "sha256": file_hash
                })

                print(f"[{index}/{len(ordered)}] OK: {dest_name}")

            details[folder.name] = records
        return run_dir, details
    except Exception:
        print(f"\nPenyiapan gagal. File sementara dipertahankan untuk pemeriksaan: {run_dir}")
        raise


def copy_folder_to_new(usb, folder_name, temp_folder):
    while True:
        name = input(f"Nama folder baru untuk hasil {folder_name}: ").strip()
        if not name or name in {".", ".."} or any(c in name for c in r'\/:*?"<>|'):
            print("Nama folder tidak valid.")
            continue
        target = usb / name
        if target.exists():
            print("Folder sudah ada; pilih nama lain.")
            continue
        break

    target.mkdir()
    staged = sorted(
        audio_files(temp_folder),
        key=lambda p: p.name.casefold()
    )

    for i, src in enumerate(staged, 1):
        dest = target / src.name

        with processing(
            f"Membuat folder baru {i}/{len(staged)}: {src.name[:40]}"
        ):
            shutil.copy2(src, dest)

            if (
                src.stat().st_size != dest.stat().st_size
                or hash_file(src) != hash_file(dest)
            ):
                raise RuntimeError(f"Verifikasi salinan gagal: {src.name}")

        print(f"[{i}/{len(staged)}] OK: {src.name}")

    return target


def replace_source(folder, temp_folder):
    staged = sorted(
        audio_files(temp_folder),
        key=lambda p: p.name.casefold()
    )

    for p in staged:
        if not p.is_file():
            raise RuntimeError(f"File sementara tidak ditemukan: {p}")
        hash_file(p)

    print(f"\nFolder yang akan diganti: {folder}")
    print(f"Jumlah MP3 sumber yang akan dihapus: {len(audio_files(folder))}")
    print(f"Jumlah MP3 hasil yang akan disalin: {len(staged)}")

    if not confirm("Konfirmasi: hapus MP3 sumber lalu salin hasil terurut?"):
        print("Replace dibatalkan; sumber tidak diubah.")
        return False

    for src in audio_files(folder):
        src.unlink()

    for i, src in enumerate(staged, 1):
        dest = folder / src.name

        with processing(
            f"Replace {folder.name} {i}/{len(staged)}: {src.name[:40]}"
        ):
            shutil.copy2(src, dest)

            if (
                src.stat().st_size != dest.stat().st_size
                or hash_file(src) != hash_file(dest)
            ):
                raise RuntimeError(
                    f"Verifikasi salinan gagal: {dest.name}\n"
                    f"JANGAN hapus folder sementara: {temp_folder}"
                )

        print(f"[{i}/{len(staged)}] OK: {src.name}")

    return True


def main():
    print("=" * 54)
    print(f"                 {APP_NAME}")
    print("=" * 54)

    usb_info = choose_usb()
    if usb_info is None:
        print("Dibatalkan.")
        pause()
        return

    usb = usb_info["path"]
    print(f"\nFlashdisk: {usb_info['label']} ({usb.drive})")

    folders = choose_folders(usb)
    if not folders:
        print("Tidak ada folder dipilih.")
        pause()
        return

    print("\nPilih lokasi penampungan sementara di SSD/HDD.")
    temp_root = choose_temp_root(usb)
    if temp_root is None:
        print("Lokasi sementara tidak dipilih atau berada di flashdisk. Batal.")
        pause()
        return

    manifest = load_manifest(usb)

    print("\nFolder terpilih:")
    for folder in folders:
        print(f"- {folder.name}: {len(audio_files(folder))} MP3")

    if not confirm("Mulai scan dan membuat penampungan sementara?"):
        print("Dibatalkan.")
        pause()
        return

    run_dir = None
    try:
        run_dir, details = prepare_temp(folders, manifest, temp_root)
        print(f"\nPenampungan sementara selesai:\n{run_dir}")

        print("\nPilih tindakan:")
        print("[1] Replace folder sumber (hapus MP3 sumber, lalu salin hasil)")
        print("[2] Buat folder baru di flashdisk (sumber tidak diubah)")
        print("[0] Batal, simpan penampungan sementara")

        choice = input("Pilihan: ").strip()
        completed = []

        if choice == "1":
            for folder in folders:
                if replace_source(folder, run_dir / folder.name):
                    completed.append(folder.name)
                else:
                    print("Replace dihentikan oleh pengguna.")
                    break

        elif choice == "2":
            for folder in folders:
                target = copy_folder_to_new(usb, folder.name, run_dir / folder.name)
                print(f"Hasil dibuat: {target}")
                completed.append(folder.name)

        elif choice == "0":
            print(f"Penampungan sementara dipertahankan: {run_dir}")

        else:
            print("Pilihan tidak dikenal; penampungan sementara dipertahankan.")

        if completed:
            current_manifest = load_manifest(usb)
            for name in completed:
                current_manifest[name] = manifest.get(name, [])
            save_manifest_atomic(usb, current_manifest)
            print("Manifest berhasil diperbarui.")

        if len(completed) == len(folders) and completed:
            if confirm("Semua hasil selesai. Hapus folder penampungan sementara beserta isinya?"):
                shutil.rmtree(run_dir)
                print("Penampungan sementara berhasil dihapus.")
            else:
                print(f"Penampungan sementara dipertahankan: {run_dir}")
        else:
            print(f"Penampungan sementara dipertahankan untuk keamanan: {run_dir}")

    except Exception as e:
        print("\nPROSES DIHENTIKAN:")
        print(e)
        if run_dir and run_dir.exists():
            print(f"Penampungan sementara dipertahankan: {run_dir}")
        print("Periksa hasil USB sebelum menjalankan proses lagi.")

    pause()


if __name__ == "__main__":
    if os.name != "nt":
        print("Program ini hanya untuk Windows.")
        pause()
    else:
        main()