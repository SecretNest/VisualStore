import { useState } from "react";

export default function Thumb({ name, fileId, size = 48 }: { name: string; fileId?: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  if (!fileId || failed) return <span className="thumb thumb-empty" style={{ width: size, height: size }} />;
  return (
    <img
      className="thumb"
      width={size}
      height={size}
      alt=""
      src={`/api/p/${name}/files/${fileId}/thumbnail`}
      onError={() => setFailed(true)}
    />
  );
}
