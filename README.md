<p align="center">
  <img src="static/img/powergrid-logo.png" alt="PowerGrid logo" width="96">
</p>

<h1 align="center">PowerGrid</h1>

<p align="center">
  Defect priority management for a transmission grid.<br>
  Operators sign in, open a sector, and move equipment faults from Open to In Progress to Resolved.
</p>

PowerGrid is a Flask application with a MySQL database. One process renders the pages, checks who is signed in, and writes the defect ledger. A separate nginx container can serve the CSS, JavaScript, and logo while gunicorn runs the application.

The mark at the top of this page is the project logo. It is the bolt-and-return artwork in `power_grid.png` with the yellow field removed, saved as `static/img/powergrid-logo.png`. That file is what the sidebar and the sign-in pages use. It is distributed with this repository under the MIT License. See [License](#license).

## What the app is for

Grid maintenance used to live in logbooks and spreadsheets. A fault on a cooling fan could be recorded without a link to the transformer it belongs to, and a deadline could pass with no record of who changed the status. PowerGrid keeps that history in one workspace per sector.

A workspace is the tenant. The demo sector is POWERGRID Punjab Transmission Grid. Inside it, locations form a tree (region, substation, bay) and equipment forms a tree (a cooling fan belongs to a transformer). A defect points at one asset, records who created it, and optionally who it is assigned to. Every status change writes an audit row.

## Demo accounts

| Email | Password | Global role | Workspace role |
| --- | --- | --- | --- |
| `admin@powergrid.in` | `powergrid123` | head | head of the Punjab workspace |
| `engineer1@powergrid.in` | `powergrid123` | engineer | engineer in the same workspace |

The invite code for that workspace is `pg-punjab-2026`.

Seeded records:

- Locations: Punjab Region, then Amritsar Substation and Ludhiana Substation, then Bay 1 and Bay 2 under Amritsar.
- Assets: Transformer T1, Transformer T2, Circuit Breaker CB-01, and Cooling Fan 1 and 2 under T1.
- Defects: an oil leak on T1 (Critical, Open, assigned to engineer1), a vibrating fan (High, In Progress), and breaker contact resistance (Medium, Open, unassigned).

`DFT-12` is not a database column. The interface prints `DFT-` plus the integer primary key. Search accepts `12` or `DFT-12` and strips the prefix before the lookup.

## Run it

Python 3.10 or newer, Docker, and Git are enough. `make` is the short path. It creates `env/`, copies `.env` from `.env.example` when that file is missing, installs dependencies, starts MySQL 8 in Docker, and runs the app.

```bash
git clone https://github.com/Tejbir7/powergrid-defect-priority-management-system.git
cd powergrid-defect-priority-management-system
make
```

Open `http://127.0.0.1:5000`. `make stop` stops the MySQL container used by this local process.

On the first start of an empty database, `python app.py` creates the tables and calls `seed_demo_data()`. If any user already exists, seeding does nothing. `flask reset-db` drops the tables, recreates them, and seeds again. `flask seed-db` only seeds.

### Docker

`make up` builds three containers and publishes only the frontend:

| Service | Role |
| --- | --- |
| `frontend` | nginx. Serves `/static/` and proxies every other request to the backend. Host port 5000. |
| `backend` | gunicorn running `app:app`. Creates tables, seeds an empty database, and runs the reminder clock in one worker. |
| `db` | MySQL 8, data kept in the `powergrid-db` volume. Not published on the host. |

```bash
make up
make down
```

Do not run `make` and `make up` at the same time. Both want port 5000.

### Tests

`make test` installs dependencies if needed and runs pytest. The suite uses an in-memory SQLite database, so it does not touch the MySQL data. To print every test name:

```bash
env/bin/python -m pytest -v
```

The tests sign in as both demo users, walk the public and signed-in pages, create and edit a defect, check the 403 responses, and call the email helpers with `mail.send` replaced by a fake. No real SMTP connection is opened.

### Environment

`.env.example` lists the variables `config.py` reads. `SECRET_KEY` and `DATABASE_URL` are required. A local URL looks like this:

```env
SECRET_KEY=generate-a-strong-secret-key
DATABASE_URL=mysql+pymysql://powergrid_user:powergrid@localhost:3306/power_db
ENABLE_SCHEDULER=True
FLASK_DEBUG=False
```

Mail settings can also be saved later from the Settings page. Those values live in `app_settings` and override the process environment when `reload_mail_config()` runs.

## How a request moves

`app.py` exposes `create_app()`. Importing the module also builds the WSGI object `app` that gunicorn loads. Running `python app.py` creates the tables, seeds demo rows when the database is empty, loads SMTP settings, registers reminder jobs, and starts the scheduler when `ENABLE_SCHEDULER` is true.

```mermaid
flowchart TD
    browser[Browser] --> flask[Flask create_app]
    flask --> config[Config from environment]
    flask --> ext[Extensions: db, csrf, bcrypt, mail, login, scheduler]
    flask --> bp[Blueprints]
    bp --> auth[auth: login, register, reset]
    bp --> ws[workspace: hub, join, switch]
    bp --> defects[defects: dashboard, ledger, history]
    bp --> assets[assets: equipment]
    bp --> admin[admin: crew and roles]
    bp --> settings[settings: reminders and SMTP]
    auth --> mysql[(MySQL)]
    ws --> mysql
    defects --> mysql
    assets --> mysql
    admin --> mysql
    settings --> mysql
    settings --> mail[Flask-Mail]
    settings --> jobs[APScheduler]
    jobs --> mail
    bp --> jinja[Jinja templates plus powergrid.css]
    jinja --> browser
```

Every signed-in page runs a context processor. It reads `session["active_workspace_id"]`, loads that workspace, and tells the sidebar which sector is active and what the user's role is there. If the session has no workspace yet, it picks the user's first membership and stores that id.

Blueprints live in `routes/`. Templates call short names such as `url_for('dashboard')`. `routes/__init__.py` registers a second URL rule for the bare name, so `dashboard` and `defects.dashboard` are the same view.

Behind nginx, the backend sets `BEHIND_PROXY=true` so Werkzeug's proxy fix trusts the forwarded host and protocol.

## Sign-in, then the workspace session

Login does not open the dashboard directly. It opens the workspaces hub. Choosing a sector stores `active_workspace_id` in the Flask session, which is a signed cookie. Later pages use that id to filter defects, assets, and crew.

```mermaid
sequenceDiagram
    actor Operator
    participant Browser
    participant Auth as routes/auth.py
    participant DB as MySQL
    participant Hub as Workspaces hub

    Operator->>Browser: POST /login email and password
    Browser->>Auth: form
    Auth->>DB: find user by email
    Auth->>Auth: bcrypt check_password
    alt password does not match
        Auth-->>Browser: login page plus error flash
    else password matches
        Auth->>Browser: login_user sets session cookie
        Auth-->>Browser: redirect /workspaces
        Browser->>Hub: GET /workspaces
        Hub->>DB: memberships for this user
        Hub-->>Browser: sector cards
        Operator->>Browser: open a sector
        Browser->>Hub: GET /workspaces/id/select
        Hub->>Browser: session active_workspace_id
        Hub-->>Browser: redirect dashboard
    end
```

Registration creates a user, hashes the password with bcrypt, signs them in, and sends them to the hub. The first account becomes a global head. Later accounts become global engineers. If the form carried an invite code, the new user is added to that workspace as an engineer and lands on the dashboard instead.

Logout is `GET /logout`. It clears the login session and returns to the sign-in page.

Password reset does not store a random token column. `services/crypto.py` derives a Fernet key from `SECRET_KEY`. Forgot-password encrypts `user_id:timestamp`. The reset page decrypts it with a one-hour TTL. If SMTP is configured, the link is emailed. If it is not, the link is shown on the forgot-password page so a demo still works. The only stored secret is `users.password_hash`.

## Creating a defect

Only a head, manager, or engineer may open `/defects/add`. A viewer is rejected. The form needs an asset in the active workspace, a description, a priority (`Low`, `Medium`, `High`, `Critical`), and a status (`Open`, `In Progress`, `Resolved`).

```mermaid
sequenceDiagram
    actor Engineer
    participant Route as defects.add_defect
    participant DB as MySQL

    Engineer->>Route: POST /defects/add
    Route->>Route: require login, approval, role
    Route->>Route: read active_workspace_id
    Route->>DB: confirm asset exists
    Route->>DB: insert defects row
    Route->>DB: insert defect_status_logs row
    Note over DB: old_status is empty, new_status is the form status, comment is Defect created
    Route-->>Engineer: redirect /defects plus success flash
```

Editing a defect writes another log row with the previous status, the new status, and the comment from the form. Deleting is limited to a head or manager, checked against the workspace role and the global role. Status-log rows are removed with the defect because the relationship uses `cascade="all, delete-orphan"`.

The dashboard counts open work, overdue items, and what was resolved today. `/search-defects?q=` matches an asset name or a defect id, then redirects to the open ledger. `scope=resolved` sends the same search to the history page.

## Roles

There are two role columns.

- `users.role` is the global role. The first registered account becomes `head`. Later registrations become `engineer`.
- `workspace_members.role` is the role inside one sector: `head`, `manager`, `engineer`, or `viewer`.

`@role_required` in `utils/auth.py` checks the global role. The delete-defect view and the sidebar check the workspace role first.

| Action | Who can do it |
| --- | --- |
| See dashboard, defects, assets, settings | Any approved user |
| Add or edit a defect | Global head, manager, or engineer |
| Delete a defect | Workspace head or manager, or global head or manager |
| Manage Sector Crew page | Global head, or workspace head or manager |
| Approve, reject, or change a global role | Global head |
| Change a workspace member's role or remove them | Any logged-in user who is not acting on themselves |
| Regenerate an invite code | Workspace head |
| Save SMTP settings or send a test email | Anyone whose global role is not `viewer` |

Joining with a code creates a workspace membership with role `engineer` and `is_approved` set.

## Email and the reminder clock

SMTP is not hard-coded to one inbox. The Settings page writes `app_settings`. `reload_mail_config()` copies those keys into `app.config` and calls `mail.init_app`. The password is stored with Fernet. If decryption fails, the raw value is kept so an older plaintext row still works.

```mermaid
sequenceDiagram
    participant Clock as APScheduler
    participant Job as run_reminders_for_user
    participant Mail as email_service
    participant SMTP as SMTP server

    Note over Clock: Started when ENABLE_SCHEDULER is true
    Clock->>Job: cron fires for one user id
    Job->>Mail: reload_mail_config
    alt MAIL_USERNAME is empty
        Job-->>Clock: return, nothing is sent
    else SMTP username is set
        Job->>Job: load enabled DEFECT_SUMMARY reminder
        Job->>Job: load defects assigned to the user that are not Resolved
        Job->>Mail: send_reminder_email
        Mail->>SMTP: message lists DFT-id, asset, priority, due date
    end
```

Each user has at most one reminder row, enforced by `UNIQUE(user_id, type)`. Frequency is daily, weekly, or monthly, with an hour and minute. Saving the form calls `schedule_user_reminder()`, which replaces the cron job `reminder_user_<id>`. On process start, `bootstrap_all_user_schedules()` re-registers every enabled reminder, because APScheduler's default store is memory and jobs disappear when the process stops. In Docker, a file lock keeps that clock in a single gunicorn worker.

`send_test_email()` is the send-test action on the settings page. It refuses to run until both server and username are saved, then builds a short message that includes the host, port, and encryption mode. `send_reminder_email()` does nothing when `MAIL_USERNAME` is empty, which is the normal state of a fresh demo.

## Data model

There are eleven tables. MySQL InnoDB stores them. A defect insert and its first status log commit in one SQLAlchemy transaction. Priority and status are limited by check constraints. Foreign keys tie equipment, people, and workspaces together. Passwords are bcrypt hashes.

```mermaid
erDiagram
    USERS ||--o{ WORKSPACE_MEMBERS : joins
    WORKSPACES ||--o{ WORKSPACE_MEMBERS : has
    WORKSPACES ||--o{ LOCATIONS : contains
    WORKSPACES ||--o{ ASSETS : owns
    WORKSPACES ||--o{ DEFECTS : tracks
    LOCATION_TYPES ||--o{ LOCATIONS : classifies
    LOCATIONS ||--o{ LOCATIONS : parent
    LOCATIONS ||--o{ ASSETS : sites
    ASSET_TYPES ||--o{ ASSETS : classifies
    ASSETS ||--o{ ASSETS : parent
    ASSETS ||--o{ DEFECTS : fails
    USERS ||--o{ DEFECTS : creates
    USERS ||--o{ DEFECTS : assigned
    USERS ||--o{ DEFECT_STATUS_LOGS : writes
    DEFECTS ||--o{ DEFECT_STATUS_LOGS : history
    USERS ||--o{ REMINDERS : schedules
    APP_SETTINGS {
        string key PK
        string value
    }
```

| Table | What it stores |
| --- | --- |
| `users` | Username, email, bcrypt `password_hash`, global `role`, `is_approved`, timestamps |
| `workspaces` | Sector name, unique invite code, creator |
| `workspace_members` | User, workspace, sector role, approval flag. Unique on `(workspace_id, user_id)` |
| `location_types` | Region, Substation, Bay |
| `locations` | Name, type, parent location, workspace |
| `asset_types` | Transformer, Circuit Breaker, Cooling Fan, and types added from the form |
| `assets` | Name, type, location, parent asset, workspace |
| `defects` | Asset, creator, assignee, priority, status, description, reported time, expected resolution, resolved time |
| `defect_status_logs` | Old status, new status, comment, author, time |
| `reminders` | One `DEFECT_SUMMARY` schedule per user |
| `app_settings` | SMTP and other runtime key-value settings. Not tied to a workspace |

`app_settings` holds the mail server, port, username, sender, and the encrypted password. Reminders are personal, not per workspace.

## Where the code lives

| Path | What it owns |
| --- | --- |
| `app.py` | Application factory, workspace context, local startup |
| `config.py` | `SECRET_KEY`, `DATABASE_URL`, mail defaults, pool settings |
| `extensions.py` | Shared SQLAlchemy, CSRF, bcrypt, mail, login, and scheduler objects |
| `gunicorn.conf.py` | Container startup: wait for MySQL, create tables, seed, start one reminder clock |
| `routes/` | HTTP endpoints |
| `models/` | The eleven tables |
| `utils/auth.py` | Login loader, `@approved_required`, `@role_required` |
| `utils/queries.py` | Defect lists, search, time windows |
| `services/email_service.py` | Load SMTP settings and send test or reminder mail |
| `services/crypto.py` | Fernet key derived from `SECRET_KEY` |
| `services/scheduler_service.py` | Cron jobs that send reminder mail |
| `cli/commands.py` | `flask seed-db` and `flask reset-db` |
| `templates/` | Pages. `base.html` is the shell. `components/sidebar.html` is the nav. `components/logo.html` is the mark |
| `static/css/powergrid.css` | Light and dark theme on top of Tabler |
| `static/img/powergrid-logo.png` | Logo used by the app and this README |
| `tests/` | Route and email tests |
| `Dockerfile.backend` | Python image for gunicorn |
| `Dockerfile.frontend` | nginx image that serves `static/` |
| `docker-compose.yml` | frontend, backend, and MySQL |
| `Makefile` | `make`, `make test`, `make up`, `make down` |
| `Procfile` | `gunicorn app:app` for a platform that reads a Procfile |

```
powergrid-defect-priority-management-system/
├── app.py
├── config.py
├── extensions.py
├── gunicorn.conf.py
├── Makefile
├── docker-compose.yml
├── Dockerfile.backend
├── Dockerfile.frontend
├── docker/frontend/nginx.conf
├── Procfile
├── requirements.txt
├── cli/
├── models/
├── routes/
├── services/
├── utils/
├── templates/
│   └── components/
│       ├── logo.html
│       ├── navbar.html
│       └── sidebar.html
├── static/
│   ├── css/powergrid.css
│   └── img/powergrid-logo.png
└── tests/
    ├── conftest.py
    ├── test_endpoints.py
    └── test_email_service.py
```

## Author

Tejbir Singh, [Tejbir7](https://github.com/Tejbir7)

## License

Copyright (c) 2026 Tejbir Singh.

This project is licensed under the MIT License. The permission covers the application source and the project logo at `static/img/powergrid-logo.png`, which is the yellow-free form of `power_grid.png`. Both files ship in this repository under that license. See [LICENSE](LICENSE) for the full text.
