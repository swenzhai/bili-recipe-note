"""Authorization for Streamlit entries that can run before or without main()."""
from __future__ import annotations

from .access_control import AccessControl


def require_legacy_admin_session(st) -> None:
    """Revalidate persisted access before a fragment, dialog or callback runs.

    No widgets are rendered here: sidebar/form commands are not legal in every
    partial rerun. A full rerun will let the main gate display the login page.
    """
    token = st.session_state.get("_legacy_admin_session")
    if AccessControl().session_valid(token, "admin"):
        return
    st.session_state.pop("_legacy_admin_session", None)
    st.rerun(scope="app")
    st.stop()
