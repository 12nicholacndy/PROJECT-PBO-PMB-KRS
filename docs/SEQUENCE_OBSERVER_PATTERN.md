# UML Sequence Diagram — Observer Pattern & Transactional Outbox (Minggu 8)

Diagram di bawah menggambarkan alur aktual eksekusi `PUT /api/jadwal/{jadwal_id}` pada codebase SEVIMA Mini (`app/routers/jadwal.py`, `app/services/scheduling_service.py`, `app/services/krs_invalidator.py`, `app/services/outbox.py`, dan `app/services/schedule_observers.py`).

> **Titik Kritis untuk Viva:** Perhatikan bahwa `COMMIT` database terjadi **sebelum** `OutboxDispatcher.run()` dan `ScheduleSubject.notify()` dipanggil. Dengan begitu:
> 1. Jika validasi bentrok/kapasitas gagal atau `COMMIT` gagal, **tidak pernah** ada notifikasi yang terkirim untuk perubahan yang tidak pernah terjadi.
> 2. Jika pengiriman notifikasi atau salah satu observer mengalami error setelah `COMMIT`, perubahan jadwal yang sah **tidak pernah dibatalkan**, dan baris `outbox_events` tetap berstatus `pending` untuk dicoba ulang (maks. 5 kali) dengan kunci idempotensi agar tidak terjadi pesan ganda.

```mermaid
sequenceDiagram
    autonumber
    actor Admin as Admin Akademik
    participant API as FastAPI Router (PUT /api/jadwal/{id})
    participant Service as SchedulingService
    participant Invalidator as KRSInvalidator
    participant DB as Database (SQLAlchemy)
    participant Dispatcher as OutboxDispatcher
    participant Subject as ScheduleSubject
    participant Observer as Observer (Student / Lecturer / Admin)
    participant Channel as LogChannel (email / sms / push)

    Admin->>API: PUT /api/jadwal/{id} (Header X-Role: admin_akademik)
    API->>Service: update(jadwal_id, data)

    rect rgb(238, 246, 255)
        Note over Service,DB: Satu Transaksi Atomik (BEFORE COMMIT)
        Service->>DB: SELECT jadwal & ambil snapshot `before`
        Service->>DB: Kunci Ruangan -> Dosen (FOR UPDATE)
        Service->>DB: Cek bentrok 3D SQL (exclude jadwal.id) & cek kapasitas
        Service->>Service: Salin atribut baru & jadwal.sequence += 1
        Service->>Invalidator: invalidate_for_kelas(db, kelas_id, alasan)
        Invalidator->>DB: Cari KRS (SUBMITTED / APPROVED) untuk kelas_id
        Invalidator->>Invalidator: krs.transition(REVISION, KRSAktor.SISTEM)
        Invalidator->>DB: UPDATE krs + INSERT krs_log + INSERT notifikasi (tanpa commit)
        Service->>DB: INSERT outbox_events (status='pending', event_id unik)
        Service->>DB: COMMIT transaksi tunggal
    end

    rect rgb(240, 253, 244)
        Note over Service,Channel: Pengiriman SETELAH COMMIT (At-Least-Once + Idempotensi)
        Service->>Dispatcher: run() (dibungkus try/except)
        Dispatcher->>DB: SELECT outbox_events WHERE status='pending' FOR UPDATE SKIP LOCKED
        Dispatcher->>Dispatcher: Rekonstruksi ScheduleEvent (frozen)
        Dispatcher->>Subject: notify(event)

        loop Untuk setiap observer di list(observers) [try/except per observer]
            Subject->>Observer: update(event)
            loop Untuk setiap penerima & 3 kanal (email, sms, push)
                Observer->>Channel: send(recipient, message, key="{event_id}:{role}:{id}:{channel}")
                alt key belum pernah terkirim
                    Channel->>Channel: Tulis log simulasi & simpan key
                else key sudah terkirim (retry)
                    Channel-->>Observer: Skip (deduplikasi idempoten)
                end
            end
        end

        Subject-->>Dispatcher: failed_observers (list nama gagal)
        alt failed_observers kosong
            Dispatcher->>DB: UPDATE outbox_events SET status='sent', attempts+=1 & COMMIT
        else ada observer gagal
            Dispatcher->>DB: attempts+=1, jika >= 5 SET status='failed' else tetap 'pending' & COMMIT
        end
    end

    Service-->>API: Jadwal terbarui (dengan sequence baru)
    API-->>Admin: HTTP 200 OK (JadwalResponse)
```

## Kesesuaian dengan Kode Aktual

- **Wiring singleton saat aplikasi start:** `init_default_schedule_subject()` dipanggil di `lifespan` (`app/main.py`) sehingga `StudentObserver`, `LecturerObserver`, dan `AdminObserver` hanya di-`attach` **sekali** saat server hidup (menghindari memory leak & notifikasi berulang).
- **Alur `DELETE /api/jadwal/{id}`:** mengikuti urutan identik (invalidasi KRS + hapus jadwal + `INSERT outbox_events` bertipe `schedule_deleted` → `COMMIT` → `OutboxDispatcher.run()`).
- **Alur Conflict Resolver UI (Tombol Terapkan):** tidak memiliki jalur khusus — tombol **Terapkan** memanggil `PUT /api/jadwal/{id}` yang sama persis dengan diagram di atas (prinsip satu pintu).
