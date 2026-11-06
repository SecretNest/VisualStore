import { Navigate } from "react-router-dom";
import { useLayout } from "../components/Layout";
import { getLastProfile } from "../lastProfile";

export default function Home() {
  const { profiles } = useLayout();
  const last = getLastProfile();
  const target =
    profiles.find((p) => p.name === last) ?? (profiles.length === 1 ? profiles[0] : null);
  return <Navigate to={target ? `/p/${target.name}/overview` : "/profiles"} replace />;
}
