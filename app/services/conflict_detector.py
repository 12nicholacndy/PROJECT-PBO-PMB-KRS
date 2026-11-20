from collections import defaultdict
from dataclasses import dataclass
from datetime import time
from itertools import combinations
from typing import Callable, Literal, Protocol, Sequence

ConflictType = Literal["room_conflict", "lecturer_conflict"]


@dataclass(frozen=True)
class Conflict:
    """Representasi konflik jadwal yang immutable/hashable agar bisa masuk set & Counter."""

    type: ConflictType
    affected: tuple[int, ...]


class JadwalLike(Protocol):
    """Kontrak atribut jadwal yang dipenuhi baik oleh JadwalSlot maupun ORM Jadwal."""

    id: int
    hari: int
    jam_mulai: time
    jam_selesai: time
    ruangan_kode: str
    dosen_id: int


@dataclass(frozen=True)
class JadwalSlot:
    """Versi ringan jadwal tanpa ketergantungan database untuk pengujian & algoritma murni."""

    id: int
    hari: int
    jam_mulai: time
    jam_selesai: time
    ruangan_kode: str
    dosen_id: int


def pasangan(id_a: int, id_b: int) -> tuple[int, int]:
    """Menormalisasi urutan dua ID agar (A, B) dan (B, A) menghasilkan objek Conflict identik."""
    return (id_a, id_b) if id_a <= id_b else (id_b, id_a)


def is_time_overlap(a: JadwalLike, b: JadwalLike) -> bool:
    """
    Memeriksa irisan interval setengah terbuka [jam_mulai, jam_selesai) pada hari yang sama.
    Memakai operator '<' ketat agar kuliah bersambung (mis. 08:00-09:40 & 09:40-11:20)
    TIDAK dianggap bentrok.
    """
    return (
        a.hari == b.hari
        and a.jam_mulai < b.jam_selesai
        and b.jam_mulai < a.jam_selesai
    )


def detect_schedule_conflict_naive(
    jadwals: Sequence[JadwalLike],
) -> list[Conflict]:
    """
    Detektor bentrok 3 dimensi versi naif — O(n^2) memakai itertools.combinations.
    Dipertahankan sebagai reference oracle untuk differential testing.
    Hanya melaporkan 'room_conflict' dan 'lecturer_conflict' (irisan waktu saja bukan bentrok).
    """
    conflicts: list[Conflict] = []
    for a, b in combinations(jadwals, 2):
        if not is_time_overlap(a, b):
            continue
        pair = pasangan(a.id, b.id)
        if a.ruangan_kode == b.ruangan_kode:
            conflicts.append(Conflict(type="room_conflict", affected=pair))
        if a.dosen_id == b.dosen_id:
            conflicts.append(Conflict(type="lecturer_conflict", affected=pair))
    return conflicts


ATURAN_KELOMPOK: dict[ConflictType, Callable[[JadwalLike], tuple[int, object]]] = {
    "room_conflict": lambda j: (j.hari, j.ruangan_kode),
    "lecturer_conflict": lambda j: (j.hari, j.dosen_id),
}


def detect_schedule_conflict(
    jadwals: Sequence[JadwalLike],
) -> list[Conflict]:
    """
    Detektor bentrok 3 dimensi teroptimasi: Grouping + Sort-and-Sweep — O(n log n + k).
    1. Kelompokkan berdasarkan (hari, ruangan_kode) dan (hari, dosen_id).
    2. Di dalam setiap kelompok, urutkan berdasarkan jam_mulai lalu lakukan sweep.
    3. Jadwal aktif dibuang ketika jam_selesai <= jadwal_baru.jam_mulai (interval setengah terbuka).
    """
    conflicts: list[Conflict] = []

    for conflict_type, key_fn in ATURAN_KELOMPOK.items():
        groups: dict[tuple[int, object], list[JadwalLike]] = defaultdict(list)
        for j in jadwals:
            groups[key_fn(j)].append(j)

        for group in groups.values():
            if len(group) < 2:
                continue
            group.sort(key=lambda j: (j.jam_mulai, j.jam_selesai, j.id))
            aktif: list[JadwalLike] = []
            for current in group:
                # Strict '>' menjaga kuliah bersambung (a.jam_selesai == current.jam_mulai) tetap lolos.
                aktif = [a for a in aktif if a.jam_selesai > current.jam_mulai]
                for other in aktif:
                    conflicts.append(
                        Conflict(
                            type=conflict_type,
                            affected=pasangan(other.id, current.id),
                        )
                    )
                aktif.append(current)

    return conflicts
