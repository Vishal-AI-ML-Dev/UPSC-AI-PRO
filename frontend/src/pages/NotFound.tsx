import { Link } from "react-router-dom"
import { useAuth } from "../lib/auth"

// Proper 404 page. Rendered both at the top level and inside the app shell for
// unknown routes, instead of silently redirecting (which used to hide broken
// links).
export function NotFound() {
  const { user } = useAuth()
  const home = user ? "/app" : "/"
  return (
    <div className="grid min-h-[50vh] place-items-center px-4">
      <div className="text-center">
        <div className="text-6xl font-extrabold text-brand-400">404</div>
        <h1 className="mt-2 text-xl font-extrabold text-fg">Page not found</h1>
        <p className="mt-1 text-sm text-muted">
          The page you&rsquo;re looking for doesn&rsquo;t exist or has moved.
        </p>
        <Link to={home} className="btn btn-brand mt-5 inline-flex">
          {user ? "Back to app" : "Back to home"}
        </Link>
      </div>
    </div>
  )
}
