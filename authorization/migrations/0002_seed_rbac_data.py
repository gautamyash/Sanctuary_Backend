"""
Seed data for the RBAC foundation (Feature 7).

Idempotent: every row is created with get_or_create(), so re-running this
migration (e.g. after a squash, or a fake-replay) never duplicates data.

Roles, permission codes/categories, and the role -> permission matrix are
taken verbatim from the original Feature 7 specification. Where the
specification names a concept with no corresponding permission code (e.g.
"Vitals", "Medicine Billing", "Own Patients", "Own Appointments"), no code is
invented here — those stay governed by the existing ownership-based business
logic in their respective apps, and are called out in the review report
rather than guessed at.
"""

from django.db import migrations

ROLES = [
    # (name, description, system_role, priority)
    ("Owner", "Full, unrestricted access to every capability in the system.", True, 100),
    ("Admin", "Administrative access to manage the platform, short of system-level settings.", True, 90),
    ("Doctor", "Clinical access: appointments, EMR, prescriptions, queue, and attendance.", True, 70),
    ("Receptionist", "Front-desk access: appointments, queue, and billing.", True, 60),
    ("Nurse", "Clinical support access: queue, attendance, and read-only EMR.", True, 60),
    ("Lab Technician", "Access to reports, lab uploads, and read-only EMR.", True, 50),
    ("Pharmacist", "Access to prescriptions.", True, 50),
    ("Accountant", "Billing, refunds, revenue, invoices, and payments.", True, 60),
    ("Patient", "Default role for patients; access to their own data only.", True, 10),
]

# (code, name, category)
PERMISSIONS = [
    # Appointments
    ("appointment.view", "View Appointments", "Appointments"),
    ("appointment.create", "Create Appointments", "Appointments"),
    ("appointment.edit", "Edit Appointments", "Appointments"),
    ("appointment.cancel", "Cancel Appointments", "Appointments"),
    ("appointment.reschedule", "Reschedule Appointments", "Appointments"),
    # EMR
    ("emr.view", "View EMR", "EMR"),
    ("emr.edit", "Edit EMR", "EMR"),
    ("emr.prescription", "Manage Prescriptions", "EMR"),
    ("emr.upload", "Upload EMR Documents", "EMR"),
    ("emr.delete", "Delete EMR Records", "EMR"),
    # Billing
    ("billing.view", "View Billing", "Billing"),
    ("billing.create", "Create Billing", "Billing"),
    ("billing.edit", "Edit Billing", "Billing"),
    ("billing.payment", "Record Payment", "Billing"),
    ("billing.refund", "Process Refund", "Billing"),
    ("billing.analytics", "View Billing Analytics", "Billing"),
    # Queue
    ("queue.view", "View Queue", "Queue"),
    ("queue.manage", "Manage Queue", "Queue"),
    # Attendance
    ("attendance.view", "View Attendance", "Attendance"),
    ("attendance.manage", "Manage Attendance", "Attendance"),
    # Analytics
    ("analytics.view", "View Analytics", "Analytics"),
    ("analytics.export", "Export Analytics", "Analytics"),
    # Users
    ("user.view", "View Users", "Users"),
    ("user.create", "Create Users", "Users"),
    ("user.edit", "Edit Users", "Users"),
    ("user.delete", "Delete Users", "Users"),
    ("user.invite", "Invite Users", "Users"),
    # Settings
    ("settings.view", "View Settings", "Settings"),
    ("settings.edit", "Edit Settings", "Settings"),
    # Reports
    ("reports.export", "Export Reports", "Reports"),
    ("reports.print", "Print Reports", "Reports"),
    # System
    ("system.admin", "System Administration", "System"),
]

_ALL_CODES = [code for code, _, _ in PERMISSIONS]

_APPOINTMENTS = [
    "appointment.view",
    "appointment.create",
    "appointment.edit",
    "appointment.cancel",
    "appointment.reschedule",
]
_EMR = ["emr.view", "emr.edit", "emr.prescription", "emr.upload", "emr.delete"]
_BILLING = [
    "billing.view",
    "billing.create",
    "billing.edit",
    "billing.payment",
    "billing.refund",
    "billing.analytics",
]
_QUEUE = ["queue.view", "queue.manage"]
_ATTENDANCE = ["attendance.view", "attendance.manage"]
_REPORTS = ["reports.export", "reports.print"]

# role name -> list of permission codes.
#
# Bare category mentions in the specification (e.g. "Appointments", "EMR",
# "Queue", "Billing") are read as the full set of codes in that category.
# "Read X" is read as X's .view code only. Sub-items that restate part of an
# already-granted category (e.g. Accountant's "Refund"/"Revenue"/"Payments"
# within "Billing") are not added again since the category grant already
# covers them.
ROLE_PERMISSION_MATRIX = {
    "Owner": _ALL_CODES,
    "Admin": [c for c in _ALL_CODES if c != "system.admin"],
    # "Own Patients" is an ownership concept, not a permission code — left
    # unmapped; ownership stays enforced by existing appointment/EMR logic.
    "Doctor": _APPOINTMENTS + _EMR + _QUEUE + _ATTENDANCE + ["billing.view"],
    # "No EMR editing" is satisfied by simply granting no EMR codes.
    "Receptionist": _APPOINTMENTS + _QUEUE + _BILLING,
    # "Vitals" has no corresponding permission code — left unmapped.
    "Nurse": _QUEUE + _ATTENDANCE + ["emr.view"],
    "Lab Technician": _REPORTS + ["emr.upload", "emr.view"],
    # "Medicine Billing" has no corresponding permission code — left
    # unmapped; only "Prescriptions" maps cleanly to emr.prescription.
    "Pharmacist": ["emr.prescription"],
    "Accountant": _BILLING,
    # "Only own: Appointments, Invoices, Records, Queue, Attendance" are all
    # ownership-scoped concepts with no corresponding permission codes —
    # left unmapped; access stays governed by existing per-user ownership
    # checks in the appointments/billing/records/queue/attendance apps.
    "Patient": [],
}


def seed_rbac_data(apps, schema_editor):
    Role = apps.get_model("authorization", "Role")
    Permission = apps.get_model("authorization", "Permission")
    RolePermission = apps.get_model("authorization", "RolePermission")

    roles_by_name = {}
    for name, description, system_role, priority in ROLES:
        role, _ = Role.objects.get_or_create(
            name=name,
            defaults={
                "description": description,
                "system_role": system_role,
                "priority": priority,
            },
        )
        roles_by_name[name] = role

    permissions_by_code = {}
    for code, name, category in PERMISSIONS:
        permission, _ = Permission.objects.get_or_create(
            code=code,
            defaults={"name": name, "category": category},
        )
        permissions_by_code[code] = permission

    for role_name, codes in ROLE_PERMISSION_MATRIX.items():
        role = roles_by_name[role_name]
        for code in codes:
            RolePermission.objects.get_or_create(
                role=role, permission=permissions_by_code[code]
            )


def unseed_rbac_data(apps, schema_editor):
    Role = apps.get_model("authorization", "Role")
    Permission = apps.get_model("authorization", "Permission")

    Role.objects.filter(name__in=[r[0] for r in ROLES]).delete()
    Permission.objects.filter(code__in=_ALL_CODES).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("authorization", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_rbac_data, unseed_rbac_data),
    ]
