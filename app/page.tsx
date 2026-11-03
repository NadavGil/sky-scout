import { Workspace } from "@/components/workspace"
import { catalogSummary, ensureFreshCatalog } from "@/lib/data/catalog"

export const dynamic = "force-dynamic"

export default async function Page() {
  const summary = catalogSummary(await ensureFreshCatalog())
  return <Workspace summary={summary} />
}
