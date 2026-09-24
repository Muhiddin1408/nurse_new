"""A11: PostgreSQL'da audit log'ni baza darajasida ham o'zgarmas qilish.

ORM'dagi taqiq (AuditLogQuerySet) faqat Django kodini to'xtatadi. Trigger
esa `psql` dan yoki boshqa servisdan kelgan UPDATE'ni ham rad etadi.
DELETE ruxsat etiladi faqat `medbron.audit_purge = on` sessiya sozlamasi
bilan (saqlash muddati tozalovi, A10). SQLite'da (lokal) — hech narsa.
"""

from django.db import migrations

FORWARD = """
CREATE OR REPLACE FUNCTION utils_auditlog_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' AND current_setting('medbron.audit_purge', true) = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'utils_auditlog: faqat INSERT (A11)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS utils_auditlog_no_change ON utils_auditlog;
CREATE TRIGGER utils_auditlog_no_change
    BEFORE UPDATE OR DELETE ON utils_auditlog
    FOR EACH ROW EXECUTE FUNCTION utils_auditlog_append_only();
"""

BACKWARD = """
DROP TRIGGER IF EXISTS utils_auditlog_no_change ON utils_auditlog;
DROP FUNCTION IF EXISTS utils_auditlog_append_only();
"""


def forward(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(FORWARD)


def backward(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(BACKWARD)


class Migration(migrations.Migration):
    dependencies = [("utils", "0003_auditlog")]

    operations = [migrations.RunPython(forward, backward)]
