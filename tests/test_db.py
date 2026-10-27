from sqlalchemy.exc import IntegrityError

from app.db import violated_constraint


def test_violated_constraint_is_none_when_the_driver_gives_no_details() -> None:
    error = IntegrityError("INSERT ...", {}, Exception("not a psycopg error"))

    assert violated_constraint(error) is None
