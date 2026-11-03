import { catalogSummary, ensureFreshCatalog } from "@/lib/data/catalog"

export const runtime = "nodejs"
export const dynamic = "force-dynamic"

export async function GET() {
  return Response.json(catalogSummary(await ensureFreshCatalog()))
}
