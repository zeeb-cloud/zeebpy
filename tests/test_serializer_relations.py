"""Tests for ModelSerializer relation handling (async `adata`).

Covers ForeignKey (raw id), reverse ForeignKey, forward ManyToMany and
reverse ManyToMany fields, which must serialize to related primary keys
instead of leaking a lazy loader / related manager object (which would
fail FastAPI response validation).
"""

import pytest

from zeeb_api.serializers import ModelSerializer
from zeeb_orm import (
    Model,
    NotSupportedError,
    close_all_connections,
    configure,
    fields,
    setup_database,
)

# Test models


class SRProject(Model):
    name = fields.CharField(max_length=100)

    class Meta:
        table_name = "sr_projects"


class SRTask(Model):
    title = fields.CharField(max_length=100)
    project = fields.ForeignKey(SRProject, related_name="tasks")

    class Meta:
        table_name = "sr_tasks"


class SRTag(Model):
    label = fields.CharField(max_length=50)

    class Meta:
        table_name = "sr_tags"


class SRArticle(Model):
    title = fields.CharField(max_length=100)
    tags = fields.ManyToMany(SRTag, related_name="articles")

    class Meta:
        table_name = "sr_articles"


MODELS = (SRProject, SRTask, SRTag, SRArticle)
THROUGH_TABLES = ("sr_articles_tags",)


# Serializers (schema generated at class definition; models already registered)


class TaskSerializer(ModelSerializer):
    class Meta:
        model = SRTask
        fields = "__all__"


class ProjectSerializer(ModelSerializer):
    class Meta:
        model = SRProject
        fields = ["id", "name", "tasks"]  # reverse FK


class ArticleSerializer(ModelSerializer):
    class Meta:
        model = SRArticle
        fields = ["id", "title", "tags"]  # forward M2M


class TagSerializer(ModelSerializer):
    class Meta:
        model = SRTag
        fields = ["id", "label", "articles"]  # reverse M2M


@pytest.fixture
async def db():
    from zeeb_orm.conf.settings import Settings
    from zeeb_orm.models.base import metadata

    Settings.reset()
    for model in MODELS:
        model._sa_table = None
        model._sa_model = None
    metadata.clear()

    configure(database={"url": "sqlite+aiosqlite:///:memory:"})
    db = await setup_database("sqlite+aiosqlite:///:memory:")
    for model in MODELS:
        model._get_table()
    await db.create_all()
    yield db
    await db.drop_all()
    await close_all_connections()
    for name in [m._meta.db_table for m in MODELS] + list(THROUGH_TABLES):
        table = metadata.tables.get(name)
        if table is not None:
            metadata.remove(table)
    for model in MODELS:
        model._sa_table = None
        model._sa_model = None
    Settings.reset()


@pytest.mark.asyncio
async def test_foreignkey_serializes_to_pk(db):
    project = await SRProject.objects.create(name="Apollo")
    task = await SRTask.objects.create(title="Build", project=project)

    fetched = await SRTask.objects.get(pk=task.pk)
    data = await TaskSerializer(instance=fetched).adata()

    assert data["project"] == project.pk
    # Validates against the generated response schema.
    TaskSerializer.ResponseSchema(**data)


@pytest.mark.asyncio
async def test_reverse_foreignkey_serializes_to_pk_list(db):
    project = await SRProject.objects.create(name="Apollo")
    t1 = await SRTask.objects.create(title="A", project=project)
    t2 = await SRTask.objects.create(title="B", project=project)

    fetched = await SRProject.objects.get(pk=project.pk)
    data = await ProjectSerializer(instance=fetched).adata()

    assert "tasks" in data
    assert set(data["tasks"]) == {t1.pk, t2.pk}
    ProjectSerializer.ResponseSchema(**data)


@pytest.mark.asyncio
async def test_forward_m2m_serializes_to_pk_list(db):
    article = await SRArticle.objects.create(title="Hello")
    tag1 = await SRTag.objects.create(label="python")
    tag2 = await SRTag.objects.create(label="async")
    await article.tags.add(tag1, tag2)

    fetched = await SRArticle.objects.get(pk=article.pk)
    data = await ArticleSerializer(instance=fetched).adata()

    assert set(data["tags"]) == {tag1.pk, tag2.pk}
    ArticleSerializer.ResponseSchema(**data)


@pytest.mark.asyncio
async def test_reverse_m2m_serializes_to_pk_list(db):
    article = await SRArticle.objects.create(title="Hello")
    tag = await SRTag.objects.create(label="python")
    await article.tags.add(tag)

    fetched = await SRTag.objects.get(pk=tag.pk)
    data = await TagSerializer(instance=fetched).adata()

    assert data["articles"] == [article.pk]
    TagSerializer.ResponseSchema(**data)


@pytest.mark.asyncio
async def test_empty_relation_serializes_to_empty_list(db):
    article = await SRArticle.objects.create(title="Lonely")

    fetched = await SRArticle.objects.get(pk=article.pk)
    data = await ArticleSerializer(instance=fetched).adata()

    assert data["tags"] == []
    ArticleSerializer.ResponseSchema(**data)


@pytest.mark.asyncio
async def test_many_adata_resolves_relations(db):
    a1 = await SRArticle.objects.create(title="One")
    await SRArticle.objects.create(title="Two")
    tag = await SRTag.objects.create(label="python")
    await a1.tags.add(tag)

    items = await SRArticle.objects.all()
    results = await ArticleSerializer(instance=items, many=True).adata()

    by_title = {r["title"]: r for r in results}
    assert by_title["One"]["tags"] == [tag.pk]
    assert by_title["Two"]["tags"] == []


@pytest.mark.asyncio
async def test_sync_data_raises_on_unresolved_relation(db):
    """Sync .data cannot load a to-many relation; it must fail loudly."""
    article = await SRArticle.objects.create(title="Hello")
    tag = await SRTag.objects.create(label="python")
    await article.tags.add(tag)

    fetched = await SRArticle.objects.get(pk=article.pk)
    with pytest.raises(NotSupportedError):
        _ = ArticleSerializer(instance=fetched).data
