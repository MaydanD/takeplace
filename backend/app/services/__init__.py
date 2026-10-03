"""Application services (PROJECT-SPEC §3.2).

Services open transactions, take the required locks, call domain logic and
apply changes through SQLAlchemy. There is no repository layer in v1.
"""
