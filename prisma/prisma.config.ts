import { defineConfig } from 'prisma/config'

export default defineConfig({
  datasourceUrl: process.env.LAB_DATABASE_URL,
})
