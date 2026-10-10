"""Use python -B -m deployment_readiness to avoid interpreter bytecode writes."""
from .preflight import main

raise SystemExit(main())
