# DB Setup Notes — Step 5 (SKIPPED)

Migration cannot run without `LAB_DATABASE_URL`. Admin must complete these steps:

1. Create PostgreSQL database `project_stl` on Hoster (83.69.248.175).
2. Set env var in `.env` at repo root:
   ```
   LAB_DATABASE_URL=postgresql://stl_user:PASSWORD@host:5432/project_stl
   ```
3. Run migration from repo root:
   ```
   npm run db:migrate
   ```
   This runs `npx prisma migrate deploy` inside the `prisma/` directory.
