# API Design

## Health and dashboard
- `GET /health`
- `GET /`

## Read APIs
- `GET /api/summary`
- `GET /api/settings`
- `GET /api/markets?limit=50`
- `GET /api/candidates?limit=50`
- `GET /api/orders?limit=50`
- `GET /api/positions?limit=50`
- `GET /api/audits?limit=10`

## Write APIs
- `POST /api/research-notes`
- `POST /api/engine/run-once`
- `POST /research-notes` (dashboard form)

## Future APIs
- `POST /api/control/pause`
- `POST /api/control/resume`
- `POST /api/research/import`
- `GET /api/metrics`
