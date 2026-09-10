import { ChevronRight } from 'lucide-react'
import { Link } from 'react-router-dom'
import { buildBreadcrumbs } from './breadcrumbItems'

export function Breadcrumbs({ pathname, period }: { pathname: string; period: string }) {
  const items = buildBreadcrumbs(pathname, period)
  return <nav className="breadcrumb-nav min-w-0 flex-1 overflow-x-auto" aria-label="面包屑导航">
    <ol className="breadcrumb-list flex h-12 min-w-max items-center px-3 text-sm">
      {items.map((item, index) => <li key={`${item.label}-${index}`} className="flex items-center">
        {index > 0 ? <ChevronRight className="mx-1 h-4 w-4 text-[var(--muted)]" aria-hidden="true" /> : null}
        {item.to
          ? <Link className="px-1 text-[var(--fg-2)] hover:text-[var(--accent)] hover:underline" to={item.to}>{item.label}</Link>
          : <span className="px-1 font-semibold text-[var(--fg)]" aria-current="page">{item.label}</span>}
      </li>)}
    </ol>
  </nav>
}
