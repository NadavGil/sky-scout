import { Workspace } from "@/components/workspace"
import { catalogSummary, getCatalog } from "@/lib/data/catalog"

export default function Page() {
  const summary = catalogSummary(getCatalog())
  return <Workspace summary={summary} />
}
