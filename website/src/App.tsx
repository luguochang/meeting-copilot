import { lazy, Suspense, type ReactNode } from 'react'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import { SiteLayout } from './components/SiteLayout'
import { HomePage } from './pages/HomePage'
import './styles/site.css'

const DocsPage = lazy(() => import('./pages/DocsPage').then((module) => ({ default: module.DocsPage })))
const ChangelogPage = lazy(() =>
  import('./pages/ChangelogPage').then((module) => ({ default: module.ChangelogPage })),
)
const NotFoundPage = lazy(() =>
  import('./pages/NotFoundPage').then((module) => ({ default: module.NotFoundPage })),
)

function lazyRoute(page: ReactNode) {
  return (
    <Suspense
      fallback={
        <main className="route-loading" id="main-content" aria-live="polite">
          <span />
          <p>正在载入页面</p>
        </main>
      }
    >
      {page}
    </Suspense>
  )
}

const router = createBrowserRouter([
  {
    element: <SiteLayout />,
    children: [
      { path: '/', element: <HomePage /> },
      { path: '/docs', element: lazyRoute(<DocsPage />) },
      { path: '/changelog', element: lazyRoute(<ChangelogPage />) },
      { path: '*', element: lazyRoute(<NotFoundPage />) },
    ],
  },
])

export default function App() {
  return <RouterProvider router={router} />
}
