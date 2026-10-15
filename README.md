# PowerGrid — Defect Priority Management System

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-2.x-000000?style=for-the-badge&logo=flask&logoColor=white)
![MySQL](https://img.shields.io/badge/MySQL-8.0_InnoDB-4479A1?style=for-the-badge&logo=mysql&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-ORM-D71F00?style=for-the-badge)
![Architecture](https://img.shields.io/badge/Schema-3NF_Normalized-blue?style=for-the-badge)
![ACID](https://img.shields.io/badge/Transactions-ACID_Compliant-success?style=for-the-badge)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

A full-stack, enterprise-grade web application for tracking, prioritizing, and resolving equipment defects across electrical power grid infrastructure. Architected with a modular **Application Factory pattern**, **Third Normal Form (3NF)** relational database schema, full **ACID compliance**, **Multi-Tenant Workspaces**, granular **Role-Based Access Control (RBAC)**, hierarchical physical location and asset trees, and automated background reminder scheduling.

---

### Why PowerGrid?
Power grid infrastructure maintenance traditionally suffered from fragmented logs across physical logbooks or disconnected spreadsheets. This created critical visibility gaps: equipment faults went unprioritized, component-level failure histories were lost, audit trails were non-existent, and SLA deadlines passed unnoticed. 

PowerGrid digitizes the entire lifecycle into a unified, secure, and normalized relational platform tailored specifically for mission-critical transmission and substation operations.

---

## Architecture Overview

PowerGrid uses a scalable, modular design following the **Application Factory pattern** with decoupled domain blueprints, service layers, and data models:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Flask Application Factory                       │
│                              (create_app)                              │
└──────┬───────────────┬─────────────────┬────────────────┬──────────────┘
       │               │                 │                │
       ▼               ▼                 ▼                ▼
 ┌───────────┐   ┌───────────┐     ┌───────────┐    ┌───────────┐
 │ Blueprints│   │ Database  │     │ Services  │    │ Extensions│
 │ (routes/) │   │ (models/) │     │(services/)│    │(SQLAlchemy│
 │ • auth    │   │ • user    │     │ • email   │    │  Bcrypt   │
 │ • workspace│  │ •workspace│     │ •scheduler│    │  CSRF     │
 │ • defects │   │ • asset   │     │ • crypto  │    │  Mail     │
 │ • assets  │   │ • defect  │     └───────────┘    │ Scheduler)│
 │ • admin   │   │ • location│                      └───────────┘
 │ • settings│   │ • reminder│
 └───────────┘   └───────────┘
```

---

## 3NF Database Architecture (11 Tables)

The relational schema strictly adheres to **Third Normal Form (3NF)**: all non-key attributes depend solely on their primary keys, with zero transitive dependencies and zero redundant data duplication.

```
                           ┌───────────────┐
                           │     USERS     │
                           └───┬───────┬───┘
                               │       │
             ┌─────────────────┘       └──────────────────┐
             │ user_id                                    │ user_id
             ▼                                            ▼
┌─────────────────────────┐                     ┌───────────────────┐
│    WORKSPACE_MEMBERS    │                     │     REMINDERS     │
└────────────┬────────────┘                     └───────────────────┘
             │ workspace_id
             ▼
┌─────────────────────────┐
│       WORKSPACES        │
└──────┬────────────┬─────┘
       │            │
       │ workspace_id
       │            │ workspace_id
       ▼            ▼
┌───────────┐ ┌───────────┐
│ LOCATIONS │ │  ASSETS   │
└─────▲─────┘ └─────┬─────┘
      │ location_id │
      └─────────────┘
             │ asset_id
             ▼
       ┌───────────┐          changed_by_id     ┌────────────────────┐
       │  DEFECTS  │───────────────────────────>│ DEFECT_STATUS_LOGS │
       └───────────┘                            └────────────────────┘
                                                     ▲
                                                     │ defect_id
                                                     └──────────────────┘

LOOKUP & SYSTEM CONFIG TABLES:
• location_types ──> locations (Region, Substation, Bay)
• asset_types    ──> assets    (Transformer, Circuit Breaker, Cooling Fan)
• app_settings   (Key-value store with TEXT values for dynamic runtime SMTP config)
```

### Table Breakdown

1. **`users`**: System user accounts (`id`, `username` [UNIQUE], `email` [UNIQUE], `password_hash`, `role`, `is_approved`, `reset_token`, `reset_token_expiry`, audit timestamps).
2. **`workspaces`**: Grid corporate/regional boundaries (`id`, `name` [UNIQUE], `invite_code` [UNIQUE], `created_by_id`, audit timestamps).
3. **`workspace_members`**: Normalized M:N mapping of users to workspaces with scoped permissions (`workspace_id`, `user_id`, `role`, `is_approved`).
4. **`location_types`**: Master category lookup (`Region`, `Substation`, `Bay`).
5. **`locations`**: Self-referencing physical hierarchy (`parent_location_id`, `workspace_id`) allowing arbitrary depth (e.g., Punjab Region &rarr; Amritsar Substation &rarr; Bay 1).
6. **`asset_types`**: Master equipment category lookup (`Transformer`, `Circuit Breaker`, `Cooling Fan`, etc.).
7. **`assets`**: Equipment registry with `asset_type_id`, `location_id`, `workspace_id`, and `parent_asset_id` (e.g., Transformer T1 &rarr; Cooling Fan 1). Child equipment automatically inherits and synchronizes locations with parents.
8. **`defects`**: Operational defect records (`workspace_id`, `asset_id`, `created_by_id`, `assigned_user_id`, `priority`, `status`, `description` [NOT NULL], `reported_at`, `expected_resolution`, `resolved_at`).
9. **`defect_status_logs`**: Immutable audit ledger recording every lifecycle state transition, author, timestamp, and justification comment.
10. **`reminders`**: Per-user background email reminder schedules (`UNIQUE(user_id, type)`).
11. **`app_settings`**: Key-value table (`key` PK, `value` TEXT) for persistent configuration that survives server restarts.

---

## ACID Compliance & Reliability

The application runs on the **MySQL InnoDB** storage engine managed through SQLAlchemy transactional sessions:

- **Atomicity**: Multi-table operations (such as registering a defect and generating its initial immutable audit status log) are wrapped in atomic transactions. Partial writes roll back completely via InnoDB Undo Logs.
- **Consistency**: Guaranteed through engine-level Foreign Key constraints, Domain `CHECK` constraints (`status IN ('Open', 'In Progress', 'Resolved')`, `priority IN ('Low', 'Medium', 'High', 'Critical')`), non-null constraints, and application validation hooks.
- **Isolation**: Executed under MySQL **`REPEATABLE-READ`** with Multi-Version Concurrency Control (MVCC) and row-level locking to prevent dirty and non-repeatable reads.
- **Durability**: Guaranteed via Write-Ahead Logging (WAL) and Redo Logs (`innodb_flush_log_at_trx_commit = 1`), preserving committed changes across power loss or crashes.

---

## Key Features

### 🏢 Multi-Tenant Workspaces
- Segment equipment, locations, defects, and teams into distinct grid workspaces (e.g., Regional Transmission grids).
- Quick join via unique shareable invitation codes.
- Seamless workspace switching via the navigation interface with persisted active session context.

### 🛡️ Role-Based Access Control (RBAC)
Granular access control enforced per workspace:

| Role | Permissions |
|---|---|
| **Head / Admin** | Complete access: user approvals, role changes, workspace settings, equipment, and defect administration |
| **Manager** | Create defects, assign field engineers, prioritize issues, and manage equipment |
| **Engineer** | Log defects, view assigned tasks, update defect statuses, and append resolution comments |
| **Viewer** | Read-only access to dashboards, equipment trees, and defect archives |

### 🌲 Hierarchical Equipment & Defect Trees
- Models physical grid hierarchy from transmission regions down to individual substation bays.
- Component-level failure tracking: attach defects directly to subcomponents (e.g., Cooling Fan) while maintaining parent asset traceability (e.g., Transformer).
- Automatic location cascading: child equipment automatically stays in sync with parent asset location updates.

### 🔄 Defect Lifecycle & Audit Ledger
- Strict lifecycle states: **Open &rarr; In Progress &rarr; Resolved**.
- Dynamic SLA calculation with visual SLA badges (Overdue, Due Soon, On Schedule).
- Permanent audit ledger: every change logs the user, old status, new status, timestamp, and notes.

### ✉️ Automated Email Reminders & Notifications
- Background automation powered by **APScheduler**.
- Per-user customizable frequency: Daily, Weekly, or Monthly digest at a designated hour.
- Personalized emails summarizing overdue and pending defects assigned to the user.
- Password recovery with time-limited cryptographic reset tokens.
- Dynamic SMTP configuration: update mail credentials on-the-fly via the Admin Settings UI without redeployment.

---

## Project Structure

```
powergrid/
├── app.py                      # Application factory (create_app) and WSGI entry point
├── config.py                   # Environment configuration & DB connection pool settings
├── extensions.py               # Initialized Flask extensions (db, bcrypt, mail, scheduler)
├── Procfile                    # Production WSGI process definition (Gunicorn)
├── requirements.txt            # Python dependencies
├── .env.example                # Template for environment variables
├── .gitignore                  # Git exclusions for Python, caches, and secrets
│
├── cli/                        # Flask CLI management commands
│   ├── __init__.py             # CLI registration hook
│   └── commands.py             # Database seed (`seed-db`) and reset (`reset-db`)
│
├── models/                     # 3NF SQLAlchemy database models
│   ├── __init__.py             # Model exports
│   ├── asset.py                # Asset & AssetType models
│   ├── defect.py               # Defect & DefectStatusLog models
│   ├── location.py             # Location & LocationType models
│   ├── reminder.py             # Per-user email reminder preferences
│   ├── settings.py             # Dynamic key-value AppSettings
│   ├── user.py                 # User authentication & password hashing
│   └── workspace.py            # Workspace & WorkspaceMember models
│
├── routes/                     # Modular Flask Blueprints
│   ├── __init__.py             # Blueprint auto-registration
│   ├── admin.py                # User approval and system administration
│   ├── assets.py               # Equipment inventory and hierarchy
│   ├── auth.py                 # Login, registration, and password recovery
│   ├── defects.py              # Defect management, SLA, and audit history
│   ├── errors.py               # Custom HTTP error pages (403, 404, 500)
│   ├── settings.py             # SMTP configuration & reminder settings
│   └── workspace.py            # Workspace switching, creation, and joining
│
├── services/                   # Decoupled business logic
│   ├── __init__.py             # Service exports
│   ├── crypto.py               # Secure token generation
│   ├── email_service.py        # Mail delivery and dynamic SMTP reloader
│   └── scheduler_service.py    # APScheduler job management
│
├── utils/                      # Helper utilities and decorators
│   ├── __init__.py             # Utility exports
│   ├── auth.py                 # RBAC decorators and Flask-Login user loader
│   └── queries.py              # Scoped workspace database queries
│
├── templates/                  # Jinja2 HTML templates (Tabler UI)
│   ├── components/             # Reusable UI partials
│   │   ├── navbar.html         # Top navigation bar with workspace switcher
│   │   └── sidebar.html        # Main navigation sidebar
│   ├── add_defect.html         # Defect entry form
│   ├── admin_users.html        # User management & role assignments
│   ├── assets.html             # Equipment tree & defect counts
│   ├── base.html               # Layout base template
│   ├── dashboard.html          # Operational KPI dashboard
│   ├── defect_history.html     # Resolved defects archive
│   ├── defects.html            # Active defects list & filtering
│   ├── forgot_password.html    # Password recovery request form
│   ├── join.html               # Workspace invitation code join page
│   ├── login.html              # Authentication portal
│   ├── pending_approval.html   # User approval waiting screen
│   ├── register.html           # Initial administrator setup / registration
│   ├── reset_password.html     # New password submission form
│   ├── settings.html           # SMTP credentials & reminder schedule preferences
│   └── workspaces.html         # Workspace directory & creation
│
└── static/                     # Tabler UI CSS, JavaScript, and asset libraries
```

---

## Local Setup

### Prerequisites
- Python 3.10 or higher
- MySQL 8.0+ running locally or remotely
- Git

### 1. Clone the Repository
```bash
git clone https://github.com/Tejbir7/powergrid-defect-priority-management-system.git
cd powergrid-defect-priority-management-system
```

### 2. Set Up Virtual Environment
```bash
python -m venv env

# On Windows (PowerShell):
.\env\Scripts\activate

# On macOS / Linux:
source env/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy the example environment configuration:
```bash
# Windows:
copy .env.example .env

# macOS / Linux:
cp .env.example .env
```

Edit `.env` with your database credentials:
```env
SECRET_KEY=generate-a-strong-secret-key
DATABASE_URL=mysql+pymysql://powergrid_user:powergrid@localhost:3306/power_db

# Optional: Email Notifications
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=your-email@gmail.com
MAIL_PASSWORD=your-app-password

# Scheduler & Debugging
ENABLE_SCHEDULER=True
FLASK_DEBUG=False
```

### 5. Initialize & Seed Database
Reset and populate the database with master lookup data, default Punjab hierarchy, equipment trees, and demo tickets:
```bash
flask reset-db
```
*(Or use `flask seed-db` on an existing schema without dropping tables).*

#### Default Demo Accounts
- **Head Administrator**: `admin@powergrid.in` / `powergrid123`
- **Field Engineer**: `engineer1@powergrid.in` / `powergrid123`
- **Default Workspace**: `POWERGRID Punjab Transmission Grid` (Invite code: `pg-punjab-2026`)

### 6. Run the Application
```bash
python app.py
```
Open **`http://127.0.0.1:5000`** in your browser.

---

## Production Deployment

The project includes a `Procfile` configured for Gunicorn WSGI servers:
```bash
gunicorn app:app
```
For production configurations:
- Set `FLASK_DEBUG=False`.
- Adjust database connection pool parameters via `DB_POOL_SIZE` and `DB_MAX_OVERFLOW`.
- If running multiple web workers, set `ENABLE_SCHEDULER=False` on worker nodes and run a dedicated scheduler worker to avoid duplicate reminder emails.

---

## Author

**Tejbir Singh**
[![GitHub](https://img.shields.io/badge/GitHub-Tejbir7-181717?style=flat&logo=github)](https://github.com/Tejbir7)

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
