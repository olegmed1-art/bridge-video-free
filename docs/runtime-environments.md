# Runtime database environments

- Vercel Production uses the Neon `production` branch.
- Vercel Preview uses the Neon `preview` branch.
- Application database connections must use Neon pooled endpoints.
- Health monitoring uses a dedicated least-privilege credential.
