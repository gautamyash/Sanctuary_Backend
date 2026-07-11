# Sanctuary Health API

Django + DRF backend for the Sanctuary Health doctor appointment app.

## Quick start (Windows)

```bash
cd health-backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python manage.py migrate
python manage.py seed          # loads specialties, doctors, schedules
python manage.py createsuperuser
python manage.py runserver 0.0.0.0:8000
```

Admin panel: http://127.0.0.1:8000/admin/ — manage doctors, schedules, and appointments with no extra code.

The Expo app on a real device must use your PC's LAN IP, e.g. `http://192.168.1.5:8000` (find it with `ipconfig`). Android emulator: `http://10.0.2.2:8000`.

## API

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/api/auth/register/` | – | `{name, email, password}` → user + JWT pair |
| POST | `/api/auth/token/` | – | `{email, password}` → `{access, refresh}` |
| POST | `/api/auth/token/refresh/` | – | `{refresh}` → new access token |
| GET | `/api/auth/me/` | JWT | Current user profile |
| GET | `/api/specialties/` | – | Specialty list with icon names |
| GET | `/api/doctors/?specialty=Cardiology&search=sarah` | – | Doctor search |
| GET | `/api/doctors/{id}/` | – | Doctor detail |
| GET | `/api/doctors/{id}/slots/?date=YYYY-MM-DD` | – | Slots with availability |
| GET | `/api/appointments/?status=confirmed` | JWT | My appointments |
| POST | `/api/appointments/` | JWT | `{doctor, date, time, reason}` → book (409 if slot taken) |
| POST | `/api/appointments/{id}/cancel/` | JWT | Cancel an upcoming appointment |

Send JWT as `Authorization: Bearer <access>`.

## Design decisions for scale

- **No double-booking, guaranteed by the database**: a conditional unique constraint on `(doctor, date, time)` for active appointments. Concurrent bookings → one 201, one 409.
- **Slots are computed, not stored**: `DoctorSchedule` defines weekly working hours; availability = schedule minus active bookings. No slot rows to maintain.
- **Stateless API + JWT**: any number of app servers can run behind a load balancer; no server-side sessions.
- **SQLite in dev, Postgres in prod**: set `DATABASE_URL` (e.g. from Railway/Render/Supabase/RDS). Connection reuse via `conn_max_age`.
- **Indexes** on `(doctor, date)` and `(patient, status)` cover the hot queries.

## Production checklist

- Set `DEBUG=False`, a real `SECRET_KEY`, explicit `ALLOWED_HOSTS` and `CORS_ALLOWED_ORIGINS`.
- Postgres via `DATABASE_URL`; run `python manage.py migrate`.
- Serve with `gunicorn config.wsgi` behind a reverse proxy; scale replicas horizontally.
- Add rate limiting (DRF throttling) and HTTPS at the proxy.
