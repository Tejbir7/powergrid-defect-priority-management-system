# PowerGrid — Defect Priority Management System

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-2.x-000000?style=for-the-badge&logo=flask&logoColor=white)
![MySQL](https://img.shields.io/badge/MySQL-8.0_InnoDB-4479A1?style=for-the-badge&logo=mysql&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-ORM-D71F00?style=for-the-badge)
![Architecture](https://img.shields.io/badge/Schema-3NF_Normalized-blue?style=for-the-badge)
![ACID](https://img.shields.io/badge/Transactions-ACID_Compliant-success?style=for-the-badge)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

A full-stack, enterprise-grade web application for tracking, prioritizing, and resolving equipment defects across power grid infrastructure. Designed strictly in **Third Normal Form (3NF)** with full **ACID compliance**, role-based access control, hierarchical physical location modeling, equipment component trees, and background reminder automation.

### Why need this?
Power grid infrastructure maintenance traditionally suffered from fragmented logs in notebooks or spreadsheets, making it difficult to prioritize critical equipment faults, track component-level failures, preserve audit histories, and enforce resolution SLAs. This system digitizes the entire workflow into a unified, normalized relational platform tailored for mission-critical grid operations.

---

## 3NF Database Architecture (9 Tables)

The database schema strictly adheres to **Third Normal Form (3NF)**: every non-key attribute depends solely on the primary key, with zero transitive dependencies and zero redundant string duplications.

```
                          ┌───────────────┐
                          │     USERS     │
                          └──────┬──┬──┬──┘
                   ┌─────────────┘  │  └──────────────┐
     created_by_id │                │                 │ changed_by_id
                   ▼                │ user_id         ▼
             ┌───────────┐          │          ┌────────────────────┐
             │  DEFECTS  │          ▼          │ DEFECT_STATUS_LOGS │
             └─────┬─────┘    ┌───────────┐    └──────────▲─────────┘
          asset_id │          │ REMINDERS │               │ defect_id
                   ▼          └───────────┘               │
             ┌───────────┐                                │
             │  ASSETS   ├────────────────────────────────┘
             └─────┬─────┘
       location_id │
                   ▼
             ┌───────────┐
             │ LOCATIONS │
             └───────────┘

LOOKUP & CONFIG TABLES:
• location_types ──> locations (Region, Substation, Bay)
• asset_types    ──> assets    (Transformer, Circuit Breaker, Cooling Fan)
• app_settings   (Key-value store with TEXT values for dynamic SMTP config)
```

### Table Breakdown

1. **`users`**: System accounts (`id`, `username` [UNIQUE], `email` [UNIQUE], `password_hash`, `role`, `is_approved`, audit timestamps).
2. **`location_types`**: Master category lookup (`Region`, `Substation`, `Bay`).
3. **`locations`**: Self-referencing physical hierarchy (`parent_location_id`) supporting infinite nesting (e.g., Punjab Region &rarr; Amritsar Substation &rarr; Bay 1).
4. **`asset_types`**: Master equipment category lookup (`Transformer`, `Circuit Breaker`, etc.).
5. **`assets`**: Individually tracked equipment with `asset_type_id`, `location_id`, and `parent_asset_id` (e.g., Transformer T1 &rarr; Cooling Fan 1). Child assets automatically inherit and sync location with parents.
6. **`defects`**: Core operational maintenance records (`asset_id`, `created_by_id`, `assigned_user_id`, `priority`, `status`, `description` [NOT NULL], `reported_at`, `expected_resolution`, `resolved_at`).
7. **`defect_status_logs`**: Immutable audit ledger recording every lifecycle transition, author, and comment.
8. **`reminders`**: Normalized per-user reminder schedules (`UNIQUE(user_id, type)`).
9. **`app_settings`**: Key-value table (`key` PK, `value` TEXT) for persistent configuration surviving dyno restarts.

---

## ACID Compliance & Reliability

The application runs on the **MySQL InnoDB** storage engine and SQLAlchemy transactional sessions:

- **Atomicity**: Multi-table operations (e.g., logging a defect and generating its initial status log) are committed atomically in a single transaction. Failures roll back completely via InnoDB Undo Logs with 0 partial writes.
- **Consistency**: Enforced at the database engine level via MySQL Foreign Key constraints, Domain `CHECK` constraints (`status IN ('Open', 'In Progress', 'Resolved')`, `priority IN ('Low', 'Medium', 'High', 'Critical')`), non-null constraints, and application invariants.
- **Isolation**: Executed under **`REPEATABLE-READ`** with Multi-Version Concurrency Control (MVCC) and row-level locking.
- **Durability**: Guaranteed via Write-Ahead Logging (WAL) and Redo Logs (`innodb_flush_log_at_trx_commit = 1`), preserving committed data across power loss or system restarts.

---

## Key Features

### Role-Based Access Control (RBAC)
| Role | Capabilities |
|---|---|
| **Head** | Full access: user administration, role assignment, system settings, and all defects |
| **Manager** | Create defects, assign engineers, set priority, update status, and manage equipment |
| **Engineer** | Report defects, update status and add comments on assigned defects |
| **Viewer** | Read-only access to dashboards, defects, and equipment summaries |

### Hierarchical Equipment & Defect Tracking
- Equipment placement models physical substations and bays.
- Component-level failure tracking: log defects against specific child parts (e.g., Cooling Fan) while retaining traceability up to parent assets (e.g., Transformer).
- Automatic location synchronization between child components and parent assets.

### Defect Lifecycle & Audit History
- Strict lifecycle states: **Open &rarr; In Progress &rarr; Resolved**.
- Reopening defects cleanly resets resolution timestamps.
- Complete audit ledger: every change logs who made the update, old/new status, timestamp, and explanation.

### Automated Email Reminders
- Background scheduling powered by **APScheduler**.
- Per-user preferences: Daily, Weekly (day of week), or Monthly (day of month) at a specified time.
- Sends personalized emails listing pending defects and SLA due dates.

---

## Project Structure

```
powergrid/
│
├── app.py                   # 3NF models, routes, scheduler, and mail integration
├── requirements.txt         # Python dependencies
├── Procfile                 # Production WSGI entry point (Gunicorn)
├── .env                     # Environment configuration
│
├── templates/               # Jinja2 HTML templates
│   ├── base.html            # Base layout with sidebar & header
│   ├── dashboard.html       # Analytics dashboard & KPIs
│   ├── defects.html         # Active defect list & details
│   ├── defect_history.html  # Archived resolved defects
│   ├── add_defect.html      # Relational defect entry form
│   ├── assets.html          # Equipment summary & defect aggregation
│   ├── admin_users.html     # User approvals & role management
│   ├── settings.html        # SMTP & reminder preferences
│   ├── login.html           # Authentication portal
│   ├── register.html        # Initial Head administrator setup
│   ├── pending_approval.html# User onboarding gate
│   └── 403.html             # Access forbidden error page
│
└── static/                  # Tabler UI assets, styles, and scripts
```

---

## Local Setup

### Prerequisites
- Python 3.10+
- MySQL 8.0 running locally
- Git

### 1. Clone the Repository
```bash
git clone https://github.com/Tejbir7/powergrid-defect-priority-management-system.git
cd powergrid-defect-priority-management-system
```

### 2. Set Up Virtual Environment
```bash
python -m venv env

# Windows
.\env\Scripts\activate

# macOS / Linux
source env/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment
Create a `.env` file in the root directory:
```env
SECRET_KEY=your-secure-random-key
DATABASE_URL=mysql+pymysql://powergrid_user:password@localhost:3306/power_db

# Optional: Email reminders
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=your-email@gmail.com
MAIL_PASSWORD=your-gmail-app-password
```

### 5. Create Database & Seed Demo Data
Initialize the database and populate the demo dataset (Punjab hierarchy, equipment, and sample defects):
```bash
flask reset-db
```
*(Or run `flask seed-db` on an existing database).*

Default seeded credentials:
- **Head Admin**: `admin@powergrid.in` / `powergrid123`
- **Field Engineer**: `engineer1@powergrid.in` / `powergrid123`

### 6. Run the Application
```bash
python app.py
```
Open `http://127.0.0.1:5000` in your browser.

---

## Author

**Tejbir Singh**
[![GitHub](https://img.shields.io/badge/GitHub-Tejbir7-181717?style=flat&logo=github)](https://github.com/Tejbir7)

---

## License

This project is licensed under the MIT License.
