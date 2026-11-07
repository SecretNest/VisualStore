import { useEffect, useState } from "react";

const RETRY_MS = 10_000;
const MAX_RETRIES = 3;

/** A thumbnail that may not exist yet (scan in progress): retry a few times before giving up. */
export default function Thumb({ name, fileId, size = 48 }: { name: string; fileId?: string; size?: number }) {
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setAttempt(0);
    setFailed(false);
  }, [fileId]);
  useEffect(() => {
    if (!failed || attempt >= MAX_RETRIES) return;
    const timer = window.setTimeout(() => {
      setFailed(false);
      setAttempt((a) => a + 1);
    }, RETRY_MS);
    return () => window.clearTimeout(timer);
  }, [failed, attempt]);
  if (!fileId || failed) return <span className="thumb thumb-empty" style={{ width: size, height: size }} />;
  const bust = attempt ? `?retry=${attempt}` : "";
  return (
    <img
      className="thumb"
      width={size}
      height={size}
      alt=""
      src={`/api/p/${name}/files/${fileId}/thumbnail${bust}`}
      onError={() => setFailed(true)}
    />
  );
}
