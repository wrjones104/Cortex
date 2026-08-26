import { useId } from "react";

interface IllustrationProps {
  size?: number | string;
  className?: string;
}

/**
 * Archie: The Cortex Librarian Owl Mascot
 * A scholarly, cute cartoon cyber-owl with round glasses and a scholar cap.
 */
export function CortexMascot({ size = 48, className = "" }: IllustrationProps) {
  const uid = useId().replace(/:/g, "_");
  const capGradId = `capGrad_${uid}`;
  const bodyGradId = `bodyGrad_${uid}`;

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={`cortex-mascot ${className}`}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={capGradId} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#38bdf8" />
          <stop offset="100%" stopColor="#2563eb" />
        </linearGradient>
        <linearGradient id={bodyGradId} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#818cf8" />
          <stop offset="50%" stopColor="#6366f1" />
          <stop offset="100%" stopColor="#4f46e5" />
        </linearGradient>
      </defs>

      {/* Little Feather Tufts / Ear Horns */}
      <polygon points="28,38 20,24 36,32" fill="#4f46e5" />
      <polygon points="72,38 80,24 64,32" fill="#4f46e5" />

      {/* Main Owl Body */}
      <path
        d="M26 44 C26 32 36 26 50 26 C64 26 74 32 74 44 C74 68 70 86 50 86 C30 86 26 68 26 44 Z"
        fill={`url(#${bodyGradId})`}
        stroke="#4338ca"
        strokeWidth="1.5"
      />

      {/* Belly / Chest Feathers */}
      <ellipse cx="50" cy="65" rx="16" ry="17" fill="#e0e7ff" />
      <path
        d="M44 58 Q50 62 56 58 M44 66 Q50 70 56 66 M44 74 Q50 78 56 74"
        stroke="#6366f1"
        strokeWidth="2"
        strokeLinecap="round"
        fill="none"
      />

      {/* Wings on Sides */}
      <path d="M24 48 C18 56 18 68 25 76" stroke="#3730a3" strokeWidth="4" strokeLinecap="round" />
      <path d="M76 48 C82 56 82 68 75 76" stroke="#3730a3" strokeWidth="4" strokeLinecap="round" />

      {/* Big Round Academic Glasses */}
      <circle cx="38" cy="46" r="10.5" fill="#ffffff" stroke="#fbbf24" strokeWidth="3" />
      <circle cx="62" cy="46" r="10.5" fill="#ffffff" stroke="#fbbf24" strokeWidth="3" />
      <path d="M48.5 46 L51.5 46" stroke="#fbbf24" strokeWidth="3" strokeLinecap="round" />

      {/* Eyes / Pupils */}
      <circle cx="39" cy="46" r="4.8" fill="#0f172a" />
      <circle cx="63" cy="46" r="4.8" fill="#0f172a" />
      {/* Sparkly Glints in Eyes */}
      <circle cx="37.5" cy="44.2" r="1.8" fill="#ffffff" />
      <circle cx="61.5" cy="44.2" r="1.8" fill="#ffffff" />
      <circle cx="41" cy="48" r="0.9" fill="#ffffff" />
      <circle cx="65" cy="48" r="0.9" fill="#ffffff" />

      {/* Golden Beak */}
      <polygon points="50,51 45,58 55,58" fill="#f59e0b" stroke="#d97706" strokeWidth="0.8" />

      {/* Scholar Mortarboard Cap */}
      <polygon points="50,13 79,23 50,33 21,23" fill={`url(#${capGradId})`} stroke="#1d4ed8" strokeWidth="1.2" />
      <polygon points="50,15 75,23 50,31 25,23" fill="#60a5fa" opacity="0.35" />
      <path d="M47 31 L47 36 Q50 38 53 36 L53 31" fill="#1d4ed8" />

      {/* Gold Tassel with Spark */}
      <path d="M72 25 L77 38" stroke="#fbbf24" strokeWidth="2.5" strokeLinecap="round" />
      <circle cx="77" cy="40" r="3.2" fill="#fbbf24" stroke="#f59e0b" strokeWidth="1" />

      {/* Little Feet */}
      <path d="M42 86 L40 90 M45 86 L45 91 M48 86 L50 90" stroke="#f59e0b" strokeWidth="2.2" strokeLinecap="round" />
      <path d="M52 86 L50 90 M55 86 L55 91 M58 86 L60 90" stroke="#f59e0b" strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  );
}

/**
 * Chat Assistant Avatar (Archie the Librarian Owl)
 */
export function ChatBotAvatar({ size = 34 }: IllustrationProps) {
  return (
    <div
      className="avatar-bot"
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        background: "linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%)",
        boxShadow: "0 2px 8px rgba(79, 70, 229, 0.35)",
        flexShrink: 0,
        overflow: "hidden",
      }}
    >
      <CortexMascot size={Number(size) * 0.95} />
    </div>
  );
}

/**
 * Chat User Avatar
 */
export function UserAvatar({ size = 34, label = "You" }: IllustrationProps & { label?: string }) {
  return (
    <div
      className="avatar-user"
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        background: "linear-gradient(135deg, #f59e0b 0%, #f97316 100%)",
        color: "#ffffff",
        fontWeight: 700,
        fontSize: typeof size === "number" ? `${size * 0.42}px` : "0.85rem",
        boxShadow: "0 2px 8px rgba(245, 158, 11, 0.35)",
        flexShrink: 0,
      }}
    >
      {label.slice(0, 1).toUpperCase()}
    </div>
  );
}

/**
 * Capture Hero Scene
 */
export function CaptureHero({ size = 110, className = "" }: IllustrationProps) {
  const uid = useId().replace(/:/g, "_");
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 120 120"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={`hero-illustration ${className}`}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={`bookGrad_${uid}`} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#10b981" />
          <stop offset="100%" stopColor="#059669" />
        </linearGradient>
        <linearGradient id={`sparkFlash_${uid}`} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#fde047" />
          <stop offset="100%" stopColor="#f59e0b" />
        </linearGradient>
      </defs>

      <circle cx="60" cy="65" r="44" fill="#10b981" fillOpacity="0.12" />
      <path d="M22 36 L24 30 L26 36 L32 38 L26 40 L24 46 L22 40 L16 38 Z" fill="#fbbf24" />
      <path d="M98 42 L100 38 L102 42 L106 44 L102 46 L100 50 L98 46 L94 44 Z" fill="#38bdf8" />
      <circle cx="30" cy="88" r="3" fill="#f43f5e" />
      <circle cx="94" cy="80" r="3" fill="#a855f7" />

      <rect x="36" y="32" width="52" height="66" rx="8" fill={`url(#bookGrad_${uid})`} />
      <rect x="42" y="36" width="42" height="58" rx="4" fill="#ffffff" />
      <line x1="48" y1="48" x2="78" y2="48" stroke="#e2e8f0" strokeWidth="2.5" strokeLinecap="round" />
      <line x1="48" y1="58" x2="78" y2="58" stroke="#e2e8f0" strokeWidth="2.5" strokeLinecap="round" />
      <line x1="48" y1="68" x2="68" y2="68" stroke="#e2e8f0" strokeWidth="2.5" strokeLinecap="round" />

      <circle cx="56" cy="56" r="2.8" fill="#0f172a" />
      <circle cx="70" cy="56" r="2.8" fill="#0f172a" />
      <path d="M60 62 Q63 66 66 62" stroke="#0f172a" strokeWidth="2" strokeLinecap="round" fill="none" />

      <path
        d="M74 16 L62 40 L72 40 L64 60 L86 34 L76 34 Z"
        fill={`url(#sparkFlash_${uid})`}
        stroke="#ffffff"
        strokeWidth="2"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * Vault Hero Scene
 */
export function VaultHero({ size = 110, className = "" }: IllustrationProps) {
  const uid = useId().replace(/:/g, "_");
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 120 120"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={`hero-illustration ${className}`}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={`chestGrad_${uid}`} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#8b5cf6" />
          <stop offset="100%" stopColor="#6366f1" />
        </linearGradient>
        <linearGradient id={`glassGrad_${uid}`} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#38bdf8" />
          <stop offset="100%" stopColor="#0284c7" />
        </linearGradient>
      </defs>

      <circle cx="60" cy="62" r="44" fill="#6366f1" fillOpacity="0.12" />
      <path d="M26 28 L28 22 L30 28 L36 30 L30 32 L28 38 L26 32 L20 30 Z" fill="#fbbf24" />
      <rect x="22" y="60" width="16" height="20" rx="3" fill="#ffffff" stroke="#cbd5e1" strokeWidth="1.5" />
      <rect x="84" y="28" width="16" height="20" rx="3" fill="#ffffff" stroke="#cbd5e1" strokeWidth="1.5" />

      <rect x="32" y="48" width="56" height="42" rx="8" fill={`url(#chestGrad_${uid})`} />
      <path d="M32 58 L88 58" stroke="#fbbf24" strokeWidth="3.5" />
      <rect x="54" y="52" width="12" height="12" rx="3" fill="#fbbf24" />
      <circle cx="60" cy="58" r="2" fill="#1e1b4b" />

      <circle cx="76" cy="46" r="16" fill={`url(#glassGrad_${uid})`} fillOpacity="0.25" stroke={`url(#glassGrad_${uid})`} strokeWidth="4" />
      <circle cx="76" cy="46" r="12" fill="#e0f2fe" fillOpacity="0.5" />
      <path d="M88 58 L104 74" stroke="#f59e0b" strokeWidth="5.5" strokeLinecap="round" />
    </svg>
  );
}

/**
 * Create Hero Scene
 */
export function CreateHero({ size = 110, className = "" }: IllustrationProps) {
  const uid = useId().replace(/:/g, "_");
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 120 120"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={`hero-illustration ${className}`}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={`bulbGrad_${uid}`} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#fde047" />
          <stop offset="100%" stopColor="#f59e0b" />
        </linearGradient>
      </defs>

      <circle cx="60" cy="58" r="44" fill="#f59e0b" fillOpacity="0.12" />
      <path d="M22 46 L24 40 L26 46 L32 48 L26 50 L24 56 L22 50 L16 48 Z" fill="#ec4899" />
      <path d="M96 32 L98 28 L100 32 L104 34 L100 36 L98 40 L96 36 L92 34 Z" fill="#10b981" />
      <circle cx="88" cy="80" r="3.5" fill="#38bdf8" />
      <circle cx="28" cy="82" r="3" fill="#f59e0b" />

      <path
        d="M60 22 C48 22 40 30 40 42 C40 50 46 56 48 62 L72 62 C74 56 80 50 80 42 C80 30 72 22 60 22 Z"
        fill={`url(#bulbGrad_${uid})`}
      />
      <rect x="50" y="64" width="20" height="8" rx="2" fill="#cbd5e1" />
      <path d="M54 72 L66 72" stroke="#94a3b8" strokeWidth="2.5" strokeLinecap="round" />

      <circle cx="53" cy="40" r="2.8" fill="#1e1b4b" />
      <circle cx="67" cy="40" r="2.8" fill="#1e1b4b" />
      <path d="M57 46 Q60 50 63 46" stroke="#1e1b4b" strokeWidth="2.2" strokeLinecap="round" fill="none" />

      <line x1="60" y1="12" x2="60" y2="17" stroke="#f59e0b" strokeWidth="3" strokeLinecap="round" />
      <line x1="36" y1="24" x2="40" y2="28" stroke="#f59e0b" strokeWidth="3" strokeLinecap="round" />
      <line x1="84" y1="24" x2="80" y2="28" stroke="#f59e0b" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

/**
 * Pending Queue Hero Scene
 */
export function PendingHero({ size = 110, className = "" }: IllustrationProps) {
  const uid = useId().replace(/:/g, "_");
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 120 120"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={`hero-illustration ${className}`}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={`cloudGrad_${uid}`} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#38bdf8" />
          <stop offset="100%" stopColor="#0ea5e9" />
        </linearGradient>
        <linearGradient id={`planeGrad_${uid}`} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#f59e0b" />
          <stop offset="100%" stopColor="#f97316" />
        </linearGradient>
      </defs>

      <circle cx="60" cy="60" r="44" fill="#38bdf8" fillOpacity="0.12" />

      <path
        d="M40 70 C34 70 30 64 32 58 C33 50 42 46 48 50 C51 40 68 38 73 48 C80 46 88 52 86 60 C90 64 88 70 82 70 Z"
        fill="#ffffff"
        stroke={`url(#cloudGrad_${uid})`}
        strokeWidth="3.5"
      />
      <circle cx="52" cy="58" r="2.2" fill="#0f172a" />
      <circle cx="66" cy="58" r="2.2" fill="#0f172a" />
      <path d="M57 62 Q59 65 61 62" stroke="#0f172a" strokeWidth="1.8" strokeLinecap="round" fill="none" />

      <path
        d="M24 92 C36 86 44 76 60 76"
        stroke="#cbd5e1"
        strokeWidth="2.5"
        strokeDasharray="4 4"
        strokeLinecap="round"
      />

      <path
        d="M62 48 L96 32 L82 72 L74 58 Z"
        fill={`url(#planeGrad_${uid})`}
        stroke="#ffffff"
        strokeWidth="2"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * Chat Hero Scene
 */
export function ChatHero({ size = 110, className = "" }: IllustrationProps) {
  return (
    <div className={`chat-hero-box ${className}`} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 10 }}>
      <CortexMascot size={size} />
    </div>
  );
}

/**
 * SignIn Hero Scene
 */
export function SignInHero({ size = 96, className = "" }: IllustrationProps) {
  return (
    <div className={`signin-hero-box ${className}`} style={{ display: "flex", justifyContent: "center", marginBottom: 8 }}>
      <CortexMascot size={size} />
    </div>
  );
}
