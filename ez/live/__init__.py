"""ez/live — Deployment, paper trading, OMS-lite, and real-broker adapters."""

from ez.live.qmt_broker import (  # re-export for package-level compatibility
    QMTBrokerConfig,
    QMTShadowBroker,
    QMTSessionKey,
    QMTSessionManager,
    QMTSessionState,
    XtQuantShadowClient,
    get_default_qmt_session_manager,
)

__all__ = [
    "QMTBrokerConfig",
    "QMTShadowBroker",
    "QMTSessionKey",
    "QMTSessionManager",
    "QMTSessionState",
    "XtQuantShadowClient",
    "get_default_qmt_session_manager",
]
