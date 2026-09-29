# Load tests

`k6-pilot.js` runs the pilot's expected load ×10 against a staging stack. The ingest path and people
working the queue run together, and the run fails if the SLO thresholds are missed (see the file header
for the volumes it assumes and how to point it at a stack).

```sh
k6 run tests/load/k6-pilot.js                 # against a local dev stack (demo sign-in)
BASE_URL=https://inbox.staging.example INTAKE_SECRET=... k6 run tests/load/k6-pilot.js
```

Record the run with the release: the k6 summary, and Prometheus over the same window (ingest lag p95,
API error ratio, job queue age). The Phase 6 exit criterion is a clean run at 10× pilot volume.
