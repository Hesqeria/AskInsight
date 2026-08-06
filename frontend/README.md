# Intelligent Data-Query Frontend

Vue 3 + ECharts 6 + Vue Router 4, built with Vite 7.

## Development

```bash
npm install
npm run dev    # http://localhost:5173
```

API requests are forwarded to `http://localhost:8000` via the Vite proxy.

## Build

```bash
npm run build  # Output goes to dist/
```

## Docker

```bash
docker build -t intelligent-analytics-frontend .
docker run -p 80:80 intelligent-analytics-frontend
```

## Features

- Natural-language queries + SSE streaming responses
- Auto-switching across 6 chart types (bar/line/pie/scatter/card/table)
- Conditional coloring (red/green for positive/negative values)
- CSV / Excel export
- Query history (localStorage)
- Quality rating (thumbs up/down)
- LLM runtime switching
- Admin panel (6 pages)
