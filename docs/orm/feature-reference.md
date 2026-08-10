# ORM Feature Reference

A condensed map of what zeeb_orm supports. Implemented features are
documented in detail in the other files under `docs/orm/`.

## Implemented

| Area | Features |
|------|----------|
| QuerySet | `filter`/`exclude` (incl. related-field traversal `author__name__startswith`, reverse relations), `order_by`, `distinct` (row-level only — no per-field arguments), `values`/`values_list` (no fields = all columns; named fields restrict the SELECT list and are labeled as given, so `values("pk")`/`values("author")` work), `only`/`defer`, `select_related`/`prefetch_related` (FK, O2O, reverse, M2M), `annotate`/`aggregate` (incl. conditional `filter=Q(...)` on aggregates; an aggregate annotation adds a `GROUP BY`, and filtering it goes to `HAVING`), slicing, `get`/`first`/`last`/`count`/`exists` (`first`/`last` fall back to `Meta.ordering`, then the primary key, so both are deterministic), `none` (matches nothing — the safe base for an unauthorized scope), `get_or_create`/`update_or_create`, `update`/`delete` (joined filters via pk-subquery), `bulk_create` (incl. `ignore_conflicts=True`)/`bulk_update`, `in_bulk`, `iterator(chunk_size)`, `union`/`intersection`/`difference`, `select_for_update`, `explain`, `raw`, `using` |
| Lookups | `exact`/`iexact`, `contains`/`icontains`, `in`, `gt`/`gte`/`lt`/`lte`, `startswith`/`istartswith`, `endswith`/`iendswith`, `range`, `isnull`, `regex`/`iregex` |
| Date transforms | `year`, `iso_year`, `month`, `day`, `week`, `week_day`, `iso_week_day`, `quarter`, `hour`, `minute`, `second`, `date`, `time` — chainable with lookups and relation traversal |
| Expressions | `F` (arithmetic, related-field traversal, datetime transforms), `Value`, `Q` (`&`/`\|`/`~`), `Case`/`When`, `Subquery`/`OuterRef`/`Exists`, `Coalesce`, `Cast`, string/date/math functions, window functions (`Window`, `RowNumber`, `Rank`, `DenseRank`, `PercentRank`, `CumeDist`, `Lag`, `Lead`, `FirstValue`, `LastValue`, `Ntile`), aggregates (`Count`/`Sum`/`Avg`/`Min`/`Max`/`StdDev`/`Variance`/`StringAgg`/`GroupConcat`) |
| Relations | `ForeignKey` (all `on_delete` variants: CASCADE, PROTECT, RESTRICT, SET_NULL, SET_DEFAULT, DO_NOTHING — DB-level DDL **and** Python-level Collector), `OneToOneField`, full `ManyToManyField` (auto through tables, `add`/`remove`/`set`/`clear`/`create`, reverse accessors, traversal, prefetch; custom `through=` read-only), self-referential FKs |
| Validation | `full_clean`/`clean_fields`/`clean`, enforced `choices` and `validators` on `save()`/`create()` (`validate=False` opt-out), `zeeb_orm.validators` module |
| Model | `save(update_fields=...)`, `delete()` (returns a `(count, {model: n})` tuple), `refresh_from_db`, `pk` |
| Managers | custom managers, `Manager.from_queryset`, `QuerySet.as_manager` |
| Meta | `table_name`/`db_table`, `abstract`, `managed`, `ordering`, `indexes`, `constraints` (Unique/Check incl. partial via condition), `unique_together`, `index_together` — all emitted into DDL; inherited from parent models except `abstract` and `table_name`/`db_table` |
| Transactions | `atomic` (nesting via savepoints; queries, `save()` and `refresh_from_db()` inside the block share the transaction session), `on_commit` (runs once after the outermost commit; discarded on savepoint rollback), `TransactionManagementError`, `IntegrityError` (constraint violations wrap the driver error) |
| Signals | `pre_save`/`post_save`/`pre_delete`/`post_delete`, `@receiver`, `send_robust`, `Signal.has_listeners`; save signals fire for `save()`, `create()`, `get_or_create()` and `update_or_create()`, but not for the set-at-a-time paths `bulk_create`/`bulk_update`/`QuerySet.update()` |
| Multi-DB | `register_database`, `.using(alias)`, `atomic(using=...)` — aliases are strict: an unregistered alias raises `ConnectionDoesNotExist`; instances remember the database they were loaded from and `save()`/`refresh_from_db()`/`delete()` target it (`using=` overrides) |
| Lookup values | ISO-format strings are coerced to date/time objects when compared against date/time columns (`in`/`range` lists included) |
| Migrations | Alembic-based autodetection (tables, columns, type/nullable, server defaults, indexes, unique constraints (named and unnamed), M2M through tables — all reversible), `RunSQL`/`RunPython`, rename operations (manual), squashing with `replaces`, dependency validation |

## Deliberately not implemented (and why)

| Feature | Reason |
|---------|--------|
| `GenericForeignKey` / contenttypes | Requires a contenttypes registry app; polymorphic FKs undermine DB-level integrity. Use explicit nullable FKs or a discriminator column. |
| Database routers (`allow_relation`/`allow_migrate`) | Multi-DB exists via explicit `.using()`; implicit routing adds magic with little benefit for API backends. |
| Proxy models / `swappable` | Niche; custom managers + `AUTH_USER_MODEL`-style resolution (zeeb_api) cover the main use cases. |
| `FileField`/`ImageField` | File storage is an application concern in async API stacks (S3 etc.); store paths/URLs in `CharField`/`JSONField`. |
| Form layer integration (`ModelForm`) | zeeb_api serializers (Pydantic) are the validation/IO layer. |
| `transaction.set_autocommit`, isolation-level control | SQLAlchemy engine options cover this at connection level (`connect_args`). |

## Known gaps (candidates for later)

- QuerySet methods that do not exist (calling them raises `AttributeError`):
  `earliest()`/`latest()`, `dates()`/`datetimes()`, `alias()`,
  `reverse()`, `contains(obj)`, `extra()`
- Filtering an aggregate annotation and a plain field inside the same
  `OR`/`NOT` group raises `NotSupportedError` — the two belong in `HAVING`
  and `WHERE`; split them into separate `filter()` calls
- `distinct(*fields)` (per-column DISTINCT) raises `NotSupportedError` —
  use `values()`/`values_list()` + `distinct()`
- Signals are exactly the four model-lifecycle ones; there are no
  many-to-many change, init, or migration signals
- JSONField has no key-path lookups (`data__key__gte=...`)
- Composite primary keys, concrete-parent table inheritance (fields are
  copied into the child table instead), `GeneratedField`
- `ArrayField`/`HStoreField`/range fields (PostgreSQL-only types)
- `QuerySet.explain(format=...)` options beyond `analyze`
- Compound date lookups on the autodetector side (no impact on queries)
- Foreign-key constraint changes (adding/removing an FK, altering `on_delete`)
  are **not** auto-detected — write a manual migration. Column, unique-constraint
  and type/nullable/default changes are auto-detected and applied via SQLite
  batch mode (`batch_alter_table`, a copy-and-swap table rebuild) on SQLite and
  in place on other dialects.
- Check constraints are not auto-migrated; they need a manual
  `AddConstraint`/`RemoveConstraint`. Unique constraints *are* auto-migrated,
  named or not.
- Window functions require SQLite ≥ 3.25 / MySQL ≥ 8; `intersection`/`difference`
  require MySQL ≥ 8.0.31
