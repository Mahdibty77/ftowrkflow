from django.db import migrations, models


class Migration(migrations.Migration):
    """Bring the frozen ``CaseEvent.action`` choices back in line with the model.

    ``EventAction.DELEGATE`` was added to cases/constants.py without a matching
    migration, so the migration state still described the pre-DELEGATE choice
    list. Choices are validation metadata only — Django never puts them in the
    database — so nothing about stored rows or reads changes here. The point is
    that ``makemigrations --check`` stops reporting drift on an unmodified
    checkout, which is what lets it be used as a CI gate, and that someone
    reading the migration history sees the same set of event actions the code
    actually writes.
    """

    dependencies = [
        ("cases", "0010_case_delegated_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="caseevent",
            name="action",
            field=models.CharField(
                choices=[
                    ("CREATE", "Case created"),
                    ("SUBMIT_TO_TECHNICAL", "Submitted to Technical"),
                    ("RETURN_TO_COMMERCIAL", "Returned to Commercial"),
                    ("ASSIGN", "Assigned to expert"),
                    ("DELEGATE", "Delegated"),
                    ("SEND_TO_SUPPLY", "Submitted to Supply"),
                    ("RETURN_TO_TECHNICAL", "Returned to Technical"),
                    ("SEND_TO_COMMERCIAL", "Submitted to Commercial"),
                    ("BUILD_TO", "TO form built"),
                    ("BUILD_PI", "PI form built"),
                    ("NEW_VERSION", "New form version"),
                    ("EDIT", "Edited"),
                    ("COMMENT", "Comment added"),
                    ("CLOSE", "Closed — sent to client"),
                    ("CANNOT_SUPPLY", "Marked cannot supply"),
                    ("APPROVE_UNSUPPLIABLE", "Cannot-supply approved"),
                    ("REJECT_UNSUPPLIABLE", "Cannot-supply rejected"),
                    ("RETURN_TO_SUPPLY", "Returned to Supply"),
                    ("FINALIZE", "Final Approved"),
                    ("REQUEST_CANCEL", "Cancellation requested"),
                    ("APPROVE_CANCEL", "Cancellation approved"),
                    ("REJECT_CANCEL", "Cancellation rejected"),
                    ("CANCEL", "Cancelled"),
                    ("BURN", "Burned"),
                    ("FINAL_CLOSE", "Final Closed"),
                ],
                max_length=30,
            ),
        ),
    ]
