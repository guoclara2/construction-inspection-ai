"""Insert an immutable snapshot or initialize a counter without replacing existing data."""
def insert_once(table, values, conflict_columns, dialect):
    if dialect in ('mysql', 'mariadb'):
        from sqlalchemy.dialects.mysql import insert
        # A no-op on duplicate keys preserves frozen snapshots and accumulated usage.
        column = conflict_columns[0]
        return insert(table).values(**values).on_duplicate_key_update(**{column: table.c[column]})
    if dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise ValueError('Unsupported database dialect: ' + dialect)
    return insert(table).values(**values).on_conflict_do_nothing(index_elements=conflict_columns)
