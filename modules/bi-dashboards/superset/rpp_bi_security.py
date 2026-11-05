"""Security manager that closes Superset's guest chart-payload gap.

Superset loads ``query_context.slice_`` through the permission-filtered chart DAO. Guest users
have dashboard-scoped access only, so the lookup returns ``None`` and the built-in
``query_context_modified`` guard treats the request as a native-filter query and skips the payload
comparison. This manager binds the saved chart without the DAO filter, requires it to belong to a
dashboard in the guest token and to query the same dataset, and then lets the built-in guard compare
metrics, columns and ordering with the saved chart.

The built-in guard does not inspect free-form ``extras.where``/``extras.having`` SQL. The provisioned
charts use none, so guest queries carrying such SQL are rejected; structured ``filters`` stay allowed.
"""

from superset.security.manager import SupersetSecurityManager


def guest_chart_error():
    from superset.errors import ErrorLevel, SupersetError, SupersetErrorType
    from superset.exceptions import SupersetSecurityException

    return SupersetSecurityException(
        SupersetError(
            error_type=SupersetErrorType.DASHBOARD_SECURITY_ACCESS_ERROR,
            message="Guest queries must use a saved chart from the embedded dashboard",
            level=ErrorLevel.WARNING,
        )
    )


def free_form_sql(queries):
    """Return whether any query carries free-form WHERE or HAVING SQL."""
    return any((query.extras or {}).get(key) for query in queries for key in ("where", "having"))


def guest_chart_allowed(chart, datasource, has_dashboard_access):
    """Return whether a saved chart may serve a guest query for the requested datasource."""
    if chart is None or datasource is None:
        return False
    if chart.datasource_id != datasource.id or chart.datasource_type != datasource.type:
        return False
    return any(has_dashboard_access(dashboard) for dashboard in chart.dashboards)


class RppSecurityManager(SupersetSecurityManager):
    def raise_for_access(self, *args, **kwargs):
        query_context = kwargs.get("query_context")
        if query_context is not None and self.is_guest_user():
            # Models load after the app is configured; this module is imported from superset_config.
            from superset import db
            from superset.models.slice import Slice

            slice_id = (query_context.form_data or {}).get("slice_id")
            chart = db.session.get(Slice, slice_id) if isinstance(slice_id, int) else None
            if free_form_sql(query_context.queries) or not guest_chart_allowed(
                chart, query_context.datasource, self.has_guest_access,
            ):
                raise guest_chart_error()
            query_context.slice_ = chart
        return super().raise_for_access(*args, **kwargs)
