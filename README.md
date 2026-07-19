# ⚡ PowerGrid — Defect Priority Management System

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-2.x-000000?style=for-the-badge&logo=flask&logoColor=white)
![MySQL](https://img.shields.io/badge/MySQL-8.0-4479A1?style=for-the-badge&logo=mysql&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-ORM-D71F00?style=for-the-badge)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

A full-stack web application for managing, tracking, and prioritising infrastructure defects in power grid operations. Built with a role-based access model to support multi-user teams — from field engineers reporting faults to heads overseeing resolution across the organisation.

---

## Screenshots

> _Add screenshots of your Dashboard, Defects list, and Settings pages here._
>
> **Tip:** Take screenshots and drag them into this section on GitHub to embed them automatically.

---

##  Features

###  Authentication & Access Control
- Secure registration flow — first user automatically becomes the **Head** of the organisation
- Invite-link based team onboarding (`/join/<invite_code>`)
- Head can **approve, reject, or change roles** of members from the admin panel
- New users are held in a **pending approval** state until the Head activates them
- Invite codes can be **regenerated** at any time to revoke old links

###  Role-Based Permissions

| Role | Capabilities |
|---|---|
| **Head** | Full access — manage users, roles, settings, all defects |
| **Manager** | Create defects, assign engineers, update status, set priority |
| **Engineer** | Report defects (default Medium priority), update assigned tasks |
| **Viewer** | Read-only access to all defects and dashboard |

###  Defect Management
- Create defects with asset name, location, description, priority, and expected resolution date
- Unique defect IDs in `DFT-123` format
- Defect lifecycle: **Open → In Progress → Resolved**
- Assign defects to specific engineers within the organisation
- Full **status change audit log** — every update is recorded with who made it and when
- Separate **Defect History** view for all resolved defects

###  Dashboard & Analytics
- Live summary cards: **Open**, **Overdue**, **Unassigned**, **Resolved Today**
- Priority breakdown: Critical / High / Medium / Low with progress bars
- Status breakdown with percentage indicators
- Engineers see their personal assigned defects on login
- **Login banner alert** showing count of pending assigned defects

###  Search & Filtering
- Search defects by asset name or ID (`DFT-123` or just `123`)
- Filter by time range: Today / Last 7 Days / Last 30 Days / Custom date range
- Filters work across both active defects and resolved history

###  Smart Email Reminders
- Per-user configurable email reminders via **APScheduler**
- Frequency options: **Daily**, **Weekly** (choose day of week), **Monthly** (choose date)
- Custom time-of-day selection (hour & minute)
- Sends a personalised list of pending assigned defects
- Reminders fire automatically in the background even when users are offline

###  Asset Tracking
- Dedicated **Assets** view showing health status per asset
- Aggregated defect count, active vs resolved breakdown, and last activity date per asset

###  Admin Settings Panel
- Live **SMTP configuration** updatable through the UI (no redeploy needed)
- SMTP password stored **encrypted** in the database using Fernet symmetric encryption
- Settings persist across server restarts via the `AppSettings` key-value model

###  Security
- Passwords hashed with **bcrypt** (Flask-Bcrypt)
- **CSRF protection** on all forms (Flask-WTF)
- Role and approval checks enforced server-side via custom decorators
- Open redirect prevention on post-login redirects
- Environment-based secret management via `.env` (never hardcoded)

---

##  Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.10+, Flask |
| **Database** | MySQL 8, Flask-SQLAlchemy (ORM) |
| **Authentication** | Flask-Login, Flask-Bcrypt |
| **Email** | Flask-Mail |
| **Scheduling** | APScheduler (BackgroundScheduler) |
| **Frontend** | HTML5, CSS3, Jinja2 templating |
| **Security** | Flask-WTF (CSRF), Fernet encryption, bcrypt |
| **Config** | python-dotenv |
| **Production Server** | Gunicorn |

---

##  Project Structure

```
powergrid/
│
├── app.py                   # Main application: models, routes, scheduler, email
├── requirements.txt         # Python dependencies
├── Procfile                 # Production server entry point (Gunicorn)
├── .env.example             # Environment variable template
│
├── templates/               # Jinja2 HTML templates
│   ├── base.html            # Base layout with navigation
│   ├── dashboard.html       # Analytics dashboard
│   ├── defects.html         # Active defects list + detail panel
│   ├── defect_history.html  # Resolved defects history
│   ├── add_defect.html      # Create new defect form
│   ├── assets.html          # Asset health summary
│   ├── admin_users.html     # User management panel
│   ├── settings.html        # SMTP & system settings
│   ├── login.html           # Login page
│   ├── register.html        # Organisation registration
│   ├── join.html            # Invite-based team join
│   └── pending_approval.html
│
└── static/
    ├── css/                 # Stylesheets
    ├── js/                  # JavaScript files
    ├── img/                 # Images and icons
    └── libs/                # Third-party libraries
```

---

##  Local Setup

### Prerequisites
- Python 3.10+
- MySQL 8.0 running locally
- Git

### 1. Clone the Repository

```bash
git clone https://github.com/Tejbir7/powergrid-defect-priority-management-system.git
cd powergrid-defect-priority-management-system
```

### 2. Create a Virtual Environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Create a `.env` file in the project root:

```env
SECRET_KEY=your-secret-key-here
DATABASE_URL=mysql+pymysql://root:yourpassword@localhost/powergrid

# Optional: Email reminders
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=youremail@gmail.com
MAIL_PASSWORD=your-app-password
```

> **Gmail users:** Use an [App Password](https://myaccount.google.com/apppasswords), not your regular password. Enable 2-Step Verification first.

### 5. Create the MySQL Database

```bash
mysql -u root -p -e "CREATE DATABASE powergrid;"
```

### 6. Initialise Tables

```bash
python -c "from app import app, db; app.app_context().push(); db.create_all(); print('Tables created!')"
```

### 7. Run the Application

```bash
flask run
```

Visit `http://127.0.0.1:5000` — the first user to register becomes the **Head** and creates the organisation workspace.

---

## 🔐 Environment Variables

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | ✅ Yes | Flask secret key for sessions & CSRF tokens |
| `DATABASE_URL` | ✅ Yes | Full MySQL connection string |
| `MAIL_SERVER` | Optional | SMTP hostname (default: `smtp.gmail.com`) |
| `MAIL_PORT` | Optional | SMTP port (default: `587`) |
| `MAIL_USE_TLS` | Optional | Enable TLS (default: `True`) |
| `MAIL_USERNAME` | Optional | SMTP login email |
| `MAIL_PASSWORD` | Optional | SMTP password or App Password |

---

##  How It Works

```
1. Head registers → creates the organisation → receives shareable invite link
2. Engineers / Managers join via invite link → await approval
3. Head approves members & assigns roles from the Admin panel
4. Managers / Engineers report defects → assign to team members
5. Engineers update defect status as work progresses
6. System automatically sends email reminders for pending assigned defects
7. Head & Managers monitor dashboard for overdue / unassigned defects
8. Resolved defects move to History with a full timestamped audit trail
```

---

##  Database Schema

```
corporations ──< users
corporations ──< defects
users ──< defects          (assigned_user_id)
users ──< defects          (created_by_id)
defects ──< defect_status_logs
users ──< defect_status_logs (changed_by_id)
app_settings               (key-value store for live SMTP config)
assets
```

---

## 👨 Author

**Tejbir Singh**

[![GitHub](https://img.shields.io/badge/GitHub-Tejbir7-181717?style=flat&logo=github)](https://github.com/Tejbir7)

---

##  License

This project is licensed under the MIT License.
