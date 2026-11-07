import { useCallback, useEffect, useState } from "react";
import { Link, NavLink, Outlet, useNavigate, useOutletContext, useParams } from "react-router-dom";
import { Unauthorized, get, post, setUnauthorizedHandler } from "../api";
import { setLastProfile } from "../lastProfile";
import type { Profile } from "../types";

export interface LayoutContext {
  profiles: Profile[];
  reloadProfiles: () => Promise<void>;
}

export function useLayout(): LayoutContext {
  return useOutletContext<LayoutContext>();
}

export default function Layout() {
  const navigate = useNavigate();
  const { name } = useParams();
  const [profiles, setProfiles] = useState<Profile[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const reloadProfiles = useCallback(async () => {
    try {
      setProfiles(await get<Profile[]>("/api/profiles"));
    } catch (err) {
      if (err instanceof Unauthorized) navigate("/login", { replace: true });
      else setLoadError(err instanceof Error ? err.message : "Could not reach the server.");
    }
  }, [navigate]);

  useEffect(() => {
    void reloadProfiles();
  }, [reloadProfiles]);

  useEffect(() => {
    // Polling pages (groups, jobs) keep fetching after the session expires: send them to login.
    setUnauthorizedHandler(() => navigate("/login", { replace: true }));
    return () => setUnauthorizedHandler(null);
  }, [navigate]);

  async function logout() {
    try {
      await post("/api/auth/logout");
    } finally {
      navigate("/login", { replace: true });
    }
  }

  function switchProfile(next: string) {
    setLastProfile(next);
    navigate(`/p/${next}/overview`);
  }

  if (profiles === null)
    return loadError ? (
      <p className="error" role="alert">
        {loadError}{" "}
        <button type="button" className="link" onClick={() => void reloadProfiles()}>
          Retry
        </button>
      </p>
    ) : (
      <p className="muted">Loading…</p>
    );

  return (
    <>
      <header className="topbar">
        <Link to="/" className="brand">
          VisualStore
        </Link>
        {profiles.length > 1 && (
          <select
            aria-label="Profile"
            value={name ?? ""}
            onChange={(e) => switchProfile(e.target.value)}
          >
            <option value="" disabled>
              Choose a profile
            </option>
            {profiles.map((p) => (
              <option key={p.name} value={p.name}>
                {p.display_name}
              </option>
            ))}
          </select>
        )}
        {profiles.length === 1 && name && (
          <span className="current">{profiles[0].display_name}</span>
        )}
        <nav>
          {name && (
            <>
              <NavLink to={`/p/${name}/overview`}>Overview</NavLink>
              <NavLink to={`/p/${name}/folders`}>Folders</NavLink>
              <NavLink to={`/p/${name}/groups`}>Groups</NavLink>
              <NavLink to={`/p/${name}/jobs`}>Jobs</NavLink>
            </>
          )}
          <Link to="/profiles">Profiles</Link>
          <button type="button" className="link" onClick={logout}>
            Log out
          </button>
        </nav>
      </header>
      <main>
        <Outlet context={{ profiles, reloadProfiles } satisfies LayoutContext} />
      </main>
    </>
  );
}
