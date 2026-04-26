from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from data_access.models import PaginatedItems

from src.tenant_api import handler as tenant_api_handler


class FakeScopedDb:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def get_item(self, _table_name: str, key: dict[str, Any]) -> dict[str, Any] | None:
        item = self.items.get((str(key["PK"]), str(key["SK"])))
        if item is None:
            return None
        return dict(item)

    def put_item(
        self,
        _table_name: str,
        item: dict[str, Any],
        *,
        condition_expression: str | None = None,
    ) -> dict[str, Any]:
        pk = str(item["PK"])
        sk = str(item["SK"])
        if (
            condition_expression
            and "attribute_not_exists" in condition_expression
            and (pk, sk) in self.items
        ):
            raise tenant_api_handler.ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
                "PutItem",
            )
        self.items[(pk, sk)] = dict(item)
        return {"Attributes": dict(item)}

    def delete_item(self, _table_name: str, key: dict[str, Any]) -> dict[str, Any]:
        pk = str(key["PK"])
        sk = str(key["SK"])
        self.items.pop((pk, sk), None)
        return {}

    def update_item(
        self,
        _table_name: str,
        key: dict[str, Any],
        update_expression: str,
        expression_attribute_values: dict[str, Any],
        *,
        expression_attribute_names: dict[str, str] | None = None,
        condition_expression: str | None = None,
    ) -> dict[str, Any]:
        pk = str(key["PK"])
        sk = str(key["SK"])
        storage_key = (pk, sk)
        existing = self.items.get(storage_key)

        if (
            condition_expression
            and "attribute_not_exists" in condition_expression
            and existing is not None
        ):
            raise tenant_api_handler.ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
                "UpdateItem",
            )
        if condition_expression and "attribute_exists" in condition_expression and existing is None:
            raise tenant_api_handler.ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "missing"}},
                "UpdateItem",
            )

        names = expression_attribute_names or {}
        item = dict(existing or {"PK": pk, "SK": sk})
        assert update_expression.startswith("SET ")
        for part in update_expression.removeprefix("SET ").split(", "):
            name_ref, value_ref = [token.strip() for token in part.split("=", 1)]
            attr_name = names.get(name_ref, name_ref.lstrip("#"))
            item[attr_name] = expression_attribute_values[value_ref]
        self.items[storage_key] = item
        return {"Attributes": dict(item)}

    def scan_all(self, _table_name: str, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self.items.values())

    def scan(
        self,
        _table_name: str | None = None,
        *,
        filter_expression: Any | None = None,
        limit: int | None = None,
        exclusive_start_key: dict[str, Any] | None = None,
        expression_attribute_names: dict[str, str] | None = None,
        expression_attribute_values: dict[str, Any] | None = None,
    ) -> PaginatedItems:
        results = [dict(item) for item in self.items.values()]

        if exclusive_start_key:
            esk_pk = exclusive_start_key.get("PK")
            esk_sk = exclusive_start_key.get("SK")
            for i, item in enumerate(results):
                if item.get("PK") == esk_pk and item.get("SK") == esk_sk:
                    results = results[i + 1 :]
                    break

        status_filter = (expression_attribute_values or {}).get(":s")
        tier_filter = (expression_attribute_values or {}).get(":t")
        if status_filter:
            results = [r for r in results if r.get("status") == status_filter]
        if tier_filter:
            results = [r for r in results if r.get("tier") == tier_filter]

        last_key = None
        if limit and len(results) > limit:
            last_key = {"PK": results[limit - 1]["PK"], "SK": results[limit - 1]["SK"]}
            results = results[:limit]

        return PaginatedItems(items=results, last_evaluated_key=last_key)

    def query(
        self,
        _table_name: str | None = None,
        *,
        sk_condition: Any | None = None,
        filter_expression: Any | None = None,
        index_name: str | None = None,
        limit: int | None = None,
        scan_index_forward: bool = True,
        exclusive_start_key: dict[str, Any] | None = None,
    ) -> PaginatedItems:
        results = [dict(item) for item in self.items.values()]
        if sk_condition:
            cls_name = type(sk_condition).__name__
            if cls_name == "BeginsWith" and hasattr(sk_condition, "_values"):
                prefix = sk_condition._values[1]
                results = [r for r in results if str(r.get("SK", "")).startswith(prefix)]
            elif cls_name == "Between" and hasattr(sk_condition, "_values"):
                v_min = sk_condition._values[1]
                v_max = sk_condition._values[2]
                results = [r for r in results if v_min <= str(r.get("SK", "")) <= v_max]
            else:
                cond_str = str(sk_condition)
                if "INVITE#" in cond_str:
                    results = [r for r in results if str(r.get("SK", "")).startswith("INVITE#")]
                elif "WEBHOOK#" in cond_str:
                    results = [r for r in results if str(r.get("SK", "")).startswith("WEBHOOK#")]

        return PaginatedItems(items=results)


class FakeSecretsManager:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.rotate_calls: list[dict[str, Any]] = []
        self.policy_calls: list[dict[str, Any]] = []

    def create_secret(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"ARN": f"arn:aws:secretsmanager:eu-west-2:111111111111:secret:{kwargs['Name']}"}

    def put_secret_value(self, **kwargs: Any) -> dict[str, Any]:
        self.rotate_calls.append(kwargs)
        return {"ARN": str(kwargs.get("SecretId", "")), "VersionId": "ver-rotated-001"}

    def put_resource_policy(self, **kwargs: Any) -> dict[str, Any]:
        self.policy_calls.append(kwargs)
        return {
            "ARN": str(kwargs.get("SecretId", "")),
            "Name": str(kwargs.get("SecretId", "")).split(":secret:", 1)[-1],
        }


class FakeEvents:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def put_events(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"FailedEntryCount": 0, "Entries": [{"EventId": "evt-1"}]}


class FakeUsageClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_tenant_usage(self, *, tenant_id: str, app_id: str | None) -> dict[str, Any]:
        self.calls.append({"tenant_id": tenant_id, "app_id": app_id})
        return {"requestsToday": 12, "budgetRemainingUsd": 34.5}


class FakeMemoryProvisioner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def provision(self, *, tenant_id: str, app_id: str) -> dict[str, Any]:
        self.calls.append({"tenant_id": tenant_id, "app_id": app_id})
        return {"memoryStoreArn": f"arn:aws:memory:eu-west-2::store/{tenant_id}"}


class FakePlatformQuotaClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response = [
            {
                "region": "eu-west-2",
                "quotaName": "ConcurrentSessions",
                "currentValue": 11.0,
                "limit": 500.0,
                "utilisationPercentage": 2.2,
            },
        ]

    def get_utilisation(
        self,
        *,
        active_region: str,
        fallback_region: str | None,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {
                "active_region": active_region,
                "fallback_region": fallback_region,
            }
        )
        return [dict(item) for item in self.response]


class FakeLambdaClient:
    def __init__(self) -> None:
        self.aliases = {"platform-bridge-dev": {"live": "10"}}
        self.versions = {
            "platform-bridge-dev": [
                {"Version": "1"},
                {"Version": "2"},
                {"Version": "10"},
                {"Version": "$LATEST"},
            ]
        }
        self.update_calls: list[dict[str, Any]] = []

    def get_alias(self, FunctionName: str, Name: str) -> dict[str, Any]:
        if FunctionName in self.aliases and Name in self.aliases[FunctionName]:
            return {"FunctionVersion": self.aliases[FunctionName][Name]}
        raise tenant_api_handler.ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}}, "GetAlias"
        )

    def get_paginator(self, operation_name: str) -> Any:
        assert operation_name == "list_versions_by_function"
        return self

    def paginate(self, FunctionName: str) -> Any:
        if FunctionName in self.versions:
            yield {"Versions": self.versions[FunctionName]}
        else:
            raise tenant_api_handler.ClientError(
                {"Error": {"Code": "ResourceNotFoundException"}}, "ListVersions"
            )

    def update_alias(self, **kwargs: Any) -> dict[str, Any]:
        self.update_calls.append(kwargs)
        return {}


class FakeDynamoDbTable:
    def __init__(self) -> None:
        self.scan_calls: list[dict[str, Any]] = []

    def scan(self, **kwargs: Any) -> dict[str, Any]:
        self.scan_calls.append(dict(kwargs))
        return {"Items": []}


class FakeDynamoDbResource:
    def __init__(self) -> None:
        self.tables: dict[str, FakeDynamoDbTable] = {}

    def Table(self, name: str) -> FakeDynamoDbTable:  # noqa: N802 - boto3 compatibility
        if name not in self.tables:
            self.tables[name] = FakeDynamoDbTable()
        return self.tables[name]


class FakeTenantScopedS3:
    def __init__(self) -> None:
        self.put_calls: list[dict[str, Any]] = []
        self.presign_calls: list[dict[str, Any]] = []

    def put_object(self, bucket: str, key: str, body: bytes, **kwargs: Any) -> None:
        self.put_calls.append(
            {
                "bucket": bucket,
                "key": key,
                "body": body,
                "kwargs": dict(kwargs),
            }
        )

    def generate_presigned_url(
        self,
        bucket: str,
        key: str,
        *,
        expires_in: int = 3600,
        client_method: str = "get_object",
    ) -> str:
        self.presign_calls.append(
            {
                "bucket": bucket,
                "key": key,
                "expires_in": expires_in,
                "client_method": client_method,
            }
        )
        return f"https://example.com/download/{key}?expires={expires_in}"


class FakeCloudWatchClient:
    def __init__(self, datapoints: list[dict[str, Any]], error: Exception | None = None) -> None:
        self.datapoints = datapoints
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def get_metric_statistics(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        return {"Datapoints": [dict(point) for point in self.datapoints]}


class FakeServiceQuotasClient:
    def __init__(self, pages: list[dict[str, Any]], error: Exception | None = None) -> None:
        self.pages = pages
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self.index = 0

    def list_service_quotas(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        if self.index >= len(self.pages):
            return {"Quotas": []}
        page = self.pages[self.index]
        self.index += 1
        return dict(page)


class FakeAwsSession:
    def __init__(
        self,
        *,
        cloudwatch_clients: dict[str, FakeCloudWatchClient] | None = None,
        service_quotas_clients: dict[str, FakeServiceQuotasClient] | None = None,
    ) -> None:
        self.cloudwatch_clients = cloudwatch_clients or {}
        self.service_quotas_clients = service_quotas_clients or {}

    def client(self, service_name: str, *, region_name: str | None = None) -> Any:
        assert region_name is not None
        if service_name == "cloudwatch":
            return self.cloudwatch_clients[region_name]
        if service_name == "service-quotas":
            return self.service_quotas_clients[region_name]
        raise AssertionError(f"Unexpected service {service_name}")


class FailingPlatformQuotaClient:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def get_utilisation(
        self,
        *,
        active_region: str,
        fallback_region: str | None,
    ) -> list[dict[str, Any]]:
        _ = active_region, fallback_region
        raise self.error


class FakeLambdaContext:
    function_name = "tenant-api"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:eu-west-2:111111111111:function:tenant-api"
    aws_request_id = "req-123"


class FakeSsm:
    def __init__(self) -> None:
        self.parameters = {
            "/platform/config/runtime-region": "eu-west-2",
        }
        self.get_calls: list[dict[str, Any]] = []
        self.put_calls: list[dict[str, Any]] = []
        self.put_error: Exception | None = None

    def get_parameter(self, *, Name: str) -> dict[str, Any]:  # noqa: N803 - boto3 compatibility
        self.get_calls.append({"Name": Name})
        return {"Parameter": {"Name": Name, "Value": self.parameters[Name]}}

    def put_parameter(self, **kwargs: Any) -> dict[str, Any]:
        self.put_calls.append(dict(kwargs))
        if self.put_error is not None:
            raise self.put_error
        self.parameters[str(kwargs["Name"])] = str(kwargs["Value"])
        return {"Version": 1}


def fixed_now_value() -> datetime:
    return datetime(2026, 2, 25, 12, 0, 0, tzinfo=UTC)


def apply_common_tenant_api_env(monkeypatch: Any, *, include_agents_table: bool = False) -> None:
    monkeypatch.setenv("AWS_REGION", "eu-west-2")
    monkeypatch.setenv("PLATFORM_ENV", "dev")
    monkeypatch.setenv("TENANTS_TABLE_NAME", "platform-tenants")
    if include_agents_table:
        monkeypatch.setenv("AGENTS_TABLE_NAME", "platform-agents")
    monkeypatch.setenv("INVOCATIONS_TABLE_NAME", "platform-invocations")
    monkeypatch.setenv("EVENT_BUS_NAME", "platform-bus")
    monkeypatch.setenv("AUDIT_EXPORT_BUCKET", "platform-audit-exports")
    monkeypatch.setenv("AUDIT_EXPORT_URL_EXPIRY_SECONDS", "1800")
    monkeypatch.setenv("TENANT_API_KEY_SECRET_PREFIX", "platform/tenants")
    monkeypatch.setenv(
        "TENANT_MGMT_ROLE_ARN",
        "arn:aws:iam::111111111111:role/platform-tenant-mgmt-dev",
    )
    monkeypatch.setenv("OPS_LOCKS_TABLE", "platform-ops-locks")
    monkeypatch.setenv("RUNTIME_REGION_PARAM", "/platform/config/runtime-region")
    monkeypatch.setenv("FALLBACK_REGION_PARAM", "/platform/config/fallback-region")


def build_tenant_api_dependencies() -> tenant_api_handler.TenantApiDependencies:
    return tenant_api_handler.TenantApiDependencies(
        secretsmanager=FakeSecretsManager(),
        events=FakeEvents(),
        ssm=FakeSsm(),
        awslambda=FakeLambdaClient(),
        usage_client=FakeUsageClient(),
        memory_provisioner=FakeMemoryProvisioner(),
        platform_quota_client=FakePlatformQuotaClient(),
    )


def build_handler_state(monkeypatch: Any, fixed_now: datetime) -> dict[str, Any]:
    db = FakeScopedDb()
    deps = build_tenant_api_dependencies()
    apply_common_tenant_api_env(monkeypatch)

    from src.tenant_api import db_factory, db_utils

    monkeypatch.setattr(db_factory, "db_for_tenant", lambda **_kwargs: db)
    monkeypatch.setattr(db_factory, "control_plane_db", lambda *_args, **_kwargs: db)
    monkeypatch.setattr(db_utils, "db_for_tenant", lambda **_kwargs: db)
    monkeypatch.setattr(db_utils, "control_plane_db", lambda *_args, **_kwargs: db)

    monkeypatch.setattr(tenant_api_handler.utils, "_OVERRIDE_NOW", fixed_now)
    return {"db": db, "deps": deps}


def build_module_state(monkeypatch: Any, fixed_now: datetime) -> dict[str, Any]:
    from src.tenant_api import db_factory, db_utils

    db = FakeScopedDb()
    deps = build_tenant_api_dependencies()
    apply_common_tenant_api_env(monkeypatch, include_agents_table=True)
    monkeypatch.setattr(db_factory, "db_for_tenant", lambda **_kwargs: db)
    monkeypatch.setattr(db_factory, "control_plane_db", lambda *_args, **_kwargs: db)
    monkeypatch.setattr(db_utils, "db_for_tenant", lambda **_kwargs: db)
    monkeypatch.setattr(db_utils, "control_plane_db", lambda *_args, **_kwargs: db)
    return {"db": db, "deps": deps}


def response_body(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(response["body"])


def invoke_handler(
    event: dict[str, Any],
    *,
    dependencies: tenant_api_handler.TenantApiDependencies,
) -> dict[str, Any]:
    return tenant_api_handler.handle_event(event, dependencies=dependencies)
