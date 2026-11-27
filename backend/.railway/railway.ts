import { bucket, defineRailway, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  const apiVolume = volume("api-volume", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, region: "iad", sizeMB: 50000 });
  const portfolioPhotos = bucket("portfolio-photos", { region: "iad" });
  const api = service("api", {
    build: { buildEnvironment: "V3", builder: "RAILPACK" },
    // moved from backend/railway.json (Config as Code ends 2026-12-01); identical values
    deploy: {
      startCommand: "sh -c 'python -m app.db_reclaim; exec uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips=* --no-access-log'",
      healthcheckPath: "/api/health/live",
      healthcheckTimeout: 120,
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 5,
    },
    replicas: { "iad": 1 },
    domains: ["api.pricechat.live"],
    volumeMounts: { "/data": apiVolume },
  });

  return project("pricechat", {
    variables: { managed: false },
    resources: [api, apiVolume, portfolioPhotos],
  });
});
