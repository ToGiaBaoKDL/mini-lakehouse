from mini_lakehouse.platform.maintenance import (
    MaintenancePlanItem,
    collect_maintenance_results,
)


def test_maintenance_result_collection_isolates_table_failures() -> None:
    failed = MaintenancePlanItem(table='"prod"."curated.github"."failed"', statements=())
    completed = MaintenancePlanItem(
        table='"prod"."analytics.engineering"."completed"',
        statements=("statement-1", "statement-2"),
    )
    completion_was_read = False

    def fail() -> int:
        raise RuntimeError("query failed")

    def succeed() -> int:
        nonlocal completion_was_read
        completion_was_read = True
        return 2

    completed_count, statement_count, failures = collect_maintenance_results(
        [(failed, fail), (completed, succeed)]
    )

    assert completion_was_read is True
    assert completed_count == 1
    assert statement_count == 2
    assert failures == ['"prod"."curated.github"."failed": query failed']
