"""Approval-token issuance -- the one piece of the approval-token lifecycle
both apps/api and apps/worker need, shared here rather than duplicated,
exactly the way both already share packages/notifiers (a real I/O package,
not a pure one) instead of one depending on the other directly.

apps/api and apps/worker remain independent deployables -- neither imports
from the other. This package is a third thing, like packages/notifiers and
packages/storage: a shared library both depend on, each supplying its own
connection/credentials. redeem_approval_token and preview_approval_token
stay in apps/api/api/approvals.py -- only the API's HTTP endpoints ever
redeem or preview a token, so there's nothing to share there.
"""

from approval_tokens.issuance import issue_approval_token

__all__ = ["issue_approval_token"]

__version__ = "0.1.0"
