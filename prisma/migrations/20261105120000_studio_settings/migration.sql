-- Settings an admin changes on /admin/studio (api/src/lib/studio-settings.ts): who paints each brain's art.
-- No row: the defaults in the code.
-- CreateTable
CREATE TABLE "settings" (
    "key" TEXT NOT NULL,
    "value" JSONB NOT NULL,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "settings_pkey" PRIMARY KEY ("key")
);
