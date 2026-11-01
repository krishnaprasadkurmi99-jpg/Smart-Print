import {
  Activity,
  ArrowDown,
  ArrowDownToLine,
  ArrowRight,
  ArrowUpRight,
  Bell,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  CreditCard,
  Copy,
  FileCheck2,
  FilePlus2,
  FileText,
  Filter,
  Gauge,
  LayoutDashboard,
  LoaderCircle,
  LogOut,
  Menu,
  MessageCircle,
  MoreHorizontal,
  Package,
  Printer,
  QrCode,
  RefreshCw,
  Search,
  Save,
  Settings,
  Share2,
  ShieldCheck,
  ShoppingBag,
  SlidersHorizontal,
  Sparkles,
  TrendingUp,
  UploadCloud,
  Users,
  X,
  type LucideIcon,
} from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { QRCodeSVG } from "qrcode.react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { FormEvent } from "react";
import {
  Link,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
import {
  api,
  formatDate,
  money,
  postFormWithProgress,
} from "./api";
import type {
  Analysis,
  ApiResult,
  InventoryItem,
  OrderDocument,
  OrderPrintSettings,
  Order,
  Printer as PrinterDevice,
  PrintingProfile,
  ReprintSearchItem,
} from "./api";

type ShopUser = { owner_id: number; shop_name: string };
type ToastState = { message: string; tone: "success" | "error" | "info" };
type NavItem = { label: string; icon: LucideIcon; section: string };

const navItems: NavItem[] = [
  { label: "Overview", icon: LayoutDashboard, section: "overview" },
  { label: "Orders", icon: ShoppingBag, section: "orders" },
  { label: "Batch printing", icon: FileCheck2, section: "batch" },
  { label: "Print queue", icon: Printer, section: "queue" },
  { label: "Customers", icon: Users, section: "customers" },
  { label: "Print history", icon: Clock3, section: "history" },
  { label: "Smart reprint", icon: Copy, section: "reprints" },
  { label: "Print profiles", icon: Save, section: "profiles" },
  { label: "Inventory", icon: Package, section: "inventory" },
  { label: "Printers", icon: Gauge, section: "printers" },
  { label: "Reports", icon: TrendingUp, section: "reports" },
  { label: "Settings", icon: Settings, section: "settings" },
];

const fadeUp = {
  hidden: { opacity: 0, y: 14 },
  visible: { opacity: 1, y: 0 },
};

function useNotice() {
  const [notice, setNotice] = useState<ToastState | null>(null);
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 4200);
    return () => window.clearTimeout(timer);
  }, [notice]);
  return { notice, notify: setNotice };
}

function App() {
  const location = useLocation();
  const { notice, notify } = useNotice();
  const trackMatch = location.pathname.match(/^\/track\/([^/]+)/);
  const pageKey = location.pathname.startsWith("/admin") || location.pathname.startsWith("/dashboard")
    ? "owner"
    : trackMatch
      ? "track"
      : "customer";

  return (
    <div className="app-shell">
      <AnimatePresence mode="wait">
        <motion.div
          key={pageKey + location.pathname}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.2 }}
        >
          <Routes location={location}>
            <Route path="/" element={<CustomerPage notify={notify} />} />
            <Route path="/admin" element={<OwnerPage notify={notify} />} />
            <Route path="/admin.html" element={<OwnerPage notify={notify} />} />
            <Route path="/dashboard" element={<OwnerPage notify={notify} />} />
            <Route path="/dashboard/" element={<OwnerPage notify={notify} />} />
            <Route path="/track/:orderId" element={<TrackingPage notify={notify} />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </motion.div>
      </AnimatePresence>
      <AnimatePresence>
        {notice && (
          <motion.div
            className={`toast toast-${notice.tone}`}
            initial={{ opacity: 0, y: 16, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8 }}
            role="status"
          >
            {notice.tone === "success" ? <CheckCircle2 size={18} /> : <MessageCircle size={18} />}
            <span>{notice.message}</span>
            <button className="icon-button toast-close" aria-label="Dismiss notification" onClick={() => notify(null)}>
              <X size={15} />
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <Link className={`brand ${compact ? "brand-compact" : ""}`} to="/" aria-label="SmartPrint home">
      <span className="brand-mark"><Printer size={19} strokeWidth={2.3} /></span>
      {!compact && (
        <span className="brand-copy">
          <strong>smartprint</strong>
          <small>PRINT, SIMPLIFIED</small>
        </span>
      )}
    </Link>
  );
}

function PublicHeader({ action = "owner" }: { action?: "owner" | "track" }) {
  return (
    <header className="public-header">
      <Brand />
      <div className="public-header-right">
        <span className="header-promise"><ShieldCheck size={15} /> Secure document handling</span>
        {action === "owner" ? (
          <Link className="button button-quiet button-small" to="/admin.html">Shop owner <ArrowRight size={15} /></Link>
        ) : (
          <Link className="button button-quiet button-small" to="/">New order <ArrowRight size={15} /></Link>
        )}
      </div>
    </header>
  );
}

function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <section className={`surface-card ${className}`}>{children}</section>;
}

function SectionHeading({
  eyebrow,
  title,
  detail,
  action,
}: {
  eyebrow?: string;
  title: string;
  detail?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="section-heading">
      <div>
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        <h2>{title}</h2>
        {detail && <p>{detail}</p>}
      </div>
      {action}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  const tone = normalized.includes("complet") ? "green"
    : normalized.includes("ready") ? "teal"
      : normalized.includes("print") ? "orange"
        : normalized.includes("accept") ? "purple"
          : normalized.includes("cancel") || normalized.includes("reject") ? "red"
            : "neutral";
  return <span className={`status-badge status-${tone}`}><span />{status || "Waiting"}</span>;
}

function CustomerPage({ notify }: { notify: (notice: ToastState | null) => void }) {
  const [files, setFiles] = useState<File[]>([]);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [forceBw, setForceBw] = useState(false);
  const [paymentMethod, setPaymentMethod] = useState<"UPI" | "Cash">("UPI");
  const [acceptedSuggestions, setAcceptedSuggestions] = useState(false);
  const [suggestionBaseline, setSuggestionBaseline] = useState<{ paper_size: string; orientation: string; duplex: boolean } | null>(null);
  const [printSettings, setPrintSettings] = useState({
    paper_size: "A4",
    orientation: "Portrait",
    color_mode: "Auto",
    duplex: false,
    quality: "Standard",
    copies: 1,
  });
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const [customerName, setCustomerName] = useState("");
  const [customerPhone, setCustomerPhone] = useState("");
  const [customerEmail, setCustomerEmail] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  const ownerId = params.get("owner_id") || localStorage.getItem("smartprint_owner_id") || "1";
  const shopName = params.get("shop") || localStorage.getItem("smartprint_shop_name") || "SmartPrint Studio";
  const displayedBw = forceBw ? analysis?.total_pages ?? 0 : analysis?.bw_pages ?? 0;
  const displayedColor = forceBw ? 0 : analysis?.color_pages ?? 0;
  const total = forceBw ? analysis?.forced_bw_price ?? 0 : analysis?.analyzed_price ?? 0;

  const addFiles = useCallback((incoming: FileList | File[]) => {
    const selected = Array.from(incoming);
    const invalid = selected.find((file) => !file.name.toLowerCase().endsWith(".pdf"));
    if (invalid) {
      notify({ tone: "error", message: `${invalid.name} is not a PDF. Choose PDF documents only.` });
      return;
    }
    if (selected.some((file) => file.size > 50 * 1024 * 1024)) {
      notify({ tone: "error", message: "Each PDF must be smaller than 50 MB." });
      return;
    }
    const merged = [...files];
    selected.forEach((file) => {
      if (!merged.some((current) => current.name === file.name && current.size === file.size && current.lastModified === file.lastModified)) {
        merged.push(file);
      }
    });
    if (merged.length > 10) {
      notify({ tone: "error", message: "You can upload up to 10 PDFs per order." });
      return;
    }
    setFiles(merged);
    setAnalysis(null);
    setAcceptedSuggestions(false);
    setSuggestionBaseline(null);
  }, [files, notify]);

  const analyzeFiles = async () => {
    if (!files.length) {
      notify({ tone: "error", message: "Add at least one PDF to get a print quote." });
      return;
    }
    setIsAnalyzing(true);
    try {
      const body = new FormData();
      files.forEach((file) => body.append("files", file));
      const result = await api<Analysis>("/analyze/", { method: "POST", body });
      setAnalysis(result);
      setForceBw(false);
      setAcceptedSuggestions(false);
      setSuggestionBaseline(null);
      notify({ tone: "success", message: "Your documents have been analyzed." });
    } catch (error) {
      notify({ tone: "error", message: error instanceof Error ? error.message : "PDF analysis failed." });
    } finally {
      setIsAnalyzing(false);
    }
  };

  const acceptAllSuggestions = () => {
    if (!analysis?.recommendations.length) return;
    setSuggestionBaseline({
      paper_size: printSettings.paper_size,
      orientation: printSettings.orientation,
      duplex: printSettings.duplex,
    });
    const shouldUseDuplex = analysis.recommendations.some((item) => item.type === "duplex");
    const paperSizes = analysis.documents.map((item) => item.paper_size).filter((value) => value !== "Custom");
    const orientations = analysis.documents.map((item) => item.orientation);
    const mostCommon = (values: string[], fallback: string) =>
      values.sort((a, b) => values.filter((value) => value === b).length - values.filter((value) => value === a).length)[0] || fallback;
    const nextSettings = {
      ...printSettings,
      duplex: printSettings.duplex || shouldUseDuplex,
      paper_size: mostCommon(paperSizes, printSettings.paper_size),
      orientation: mostCommon(orientations, printSettings.orientation),
      color_mode: analysis.color_pages && analysis.bw_pages ? "Mixed" : analysis.color_pages ? "Color" : printSettings.color_mode,
    };
    const confirmation = [
      shouldUseDuplex ? "enable two-sided printing" : "",
      `use ${nextSettings.paper_size} ${nextSettings.orientation.toLowerCase()} paper`,
      nextSettings.color_mode !== printSettings.color_mode ? `set color mode to ${nextSettings.color_mode}` : "",
    ].filter(Boolean).join(", ");
    if (!window.confirm(`Apply these print suggestions: ${confirmation}?`)) return;
    setPrintSettings(nextSettings);
    setAcceptedSuggestions(true);
    setForceBw(nextSettings.color_mode === "B&W");
  };

  const updatePrintSettings = (update: Partial<typeof printSettings>) => {
    setPrintSettings((current) => ({ ...current, ...update }));
    setAcceptedSuggestions(false);
  };

  const submitOrder = async (event: FormEvent) => {
    event.preventDefault();
    if (!analysis || files.length === 0) return;
    if (!customerName.trim() || !customerPhone.trim()) {
      notify({ tone: "error", message: "Enter your name and phone number to continue." });
      return;
    }
    setIsSubmitting(true);
    setUploadProgress(0);
    const body = new FormData();
    files.forEach((file) => body.append("files", file));
    body.append("owner_id", ownerId);
    body.append("payment_method", paymentMethod);
    body.append("force_bw", String(forceBw));
    body.append("customer_name", customerName.trim());
    body.append("customer_phone", customerPhone.trim());
    body.append("customer_email", customerEmail.trim());
    body.append("print_settings", JSON.stringify({
      ...printSettings,
      color_mode: forceBw ? "B&W" : printSettings.color_mode,
    }));
    try {
      const result = await postFormWithProgress<{
        status: string;
        order_id: string;
        tracking_token: string;
        pickup_pin: string;
        total_price: number;
        total_pages: number;
        bw_pages: number;
        color_pages: number;
      }>("/upload/", body, setUploadProgress);
      localStorage.setItem(`smartprint_order_${result.order_id}`, JSON.stringify({
        pin: result.pickup_pin,
        shopName,
        customerName: customerName.trim(),
        paymentMethod,
      }));
      notify({ tone: "success", message: "Order placed. Your tracking details are ready." });
      window.location.assign(`/track/${encodeURIComponent(result.order_id)}?token=${encodeURIComponent(result.tracking_token)}`);
    } catch (error) {
      notify({ tone: "error", message: error instanceof Error ? error.message : "Could not send your order." });
      setIsSubmitting(false);
    }
  };

  const removeFile = (name: string, lastModified: number) => {
    setFiles((current) => current.filter((file) => file.name !== name || file.lastModified !== lastModified));
    setAnalysis(null);
    setAcceptedSuggestions(false);
    setSuggestionBaseline(null);
  };

  return (
    <div className="public-page">
      <PublicHeader />
      <main className="customer-main">
        <motion.section className="customer-hero" initial="hidden" animate="visible" variants={fadeUp} transition={{ duration: 0.5 }}>
          <div className="hero-copy">
            <span className="eyebrow"><span className="live-dot" /> PRINT SHOP · ONLINE</span>
            <h1>Good work deserves<br /><span>a great print.</span></h1>
            <p>Upload your documents, get a clear quote, and pick up when it’s ready. A better way to print, from start to finish.</p>
            <div className="hero-trust">
              <span><Check size={15} /> Instant page analysis</span>
              <span><Check size={15} /> Secure order tracking</span>
            </div>
          </div>
          <div className="hero-art" aria-hidden="true">
            <div className="hero-orbit orbit-one" />
            <div className="hero-orbit orbit-two" />
            <motion.div className="hero-paper paper-back" animate={{ rotate: [-8, -5, -8], y: [0, -5, 0] }} transition={{ duration: 6, repeat: Infinity }} />
            <motion.div className="hero-paper paper-front" animate={{ rotate: [5, 2, 5], y: [0, -9, 0] }} transition={{ duration: 5, repeat: Infinity }}>
              <div className="paper-line line-short" /><div className="paper-line" /><div className="paper-line" />
              <div className="paper-chart"><span /><span /><span /><span /><span /></div>
              <div className="paper-line line-mid" /><div className="paper-line line-short" />
            </motion.div>
            <div className="floating-chip chip-fast"><Sparkles size={14} /> Ready in ~2 hours</div>
            <div className="floating-chip chip-pages"><FileText size={14} /> Page-by-page pricing</div>
          </div>
        </motion.section>

        <div className="customer-workspace">
          <div className="customer-flow">
            <Card className="upload-card">
              <SectionHeading eyebrow="01 / YOUR DOCUMENTS" title="Start with your files" detail="PDF format · Up to 10 files · 50 MB each" />
              <div
                className={`dropzone ${isDragging ? "dropzone-active" : ""} ${files.length ? "dropzone-filled" : ""}`}
                onDragOver={(event) => { event.preventDefault(); setIsDragging(true); }}
                onDragLeave={() => setIsDragging(false)}
                onDrop={(event) => { event.preventDefault(); setIsDragging(false); addFiles(event.dataTransfer.files); }}
              >
                <input ref={inputRef} type="file" accept=".pdf,application/pdf" multiple hidden onChange={(event) => {
                  if (event.currentTarget.files) addFiles(event.currentTarget.files);
                  event.currentTarget.value = "";
                }} />
                <div className="drop-icon"><UploadCloud size={24} /></div>
                <div>
                  <strong>Drop your PDFs here</strong>
                  <p>or select from your device</p>
                </div>
                <button className="button button-secondary" type="button" onClick={() => inputRef.current?.click()}>
                  Choose files
                </button>
              </div>

              {files.length > 0 && (
                <div className="file-list">
                  <div className="file-list-head">
                    <span>{files.length} document{files.length !== 1 ? "s" : ""} selected</span>
                    <button className="text-button" onClick={() => { setFiles([]); setAnalysis(null); setAcceptedSuggestions(false); setSuggestionBaseline(null); }}>Remove all</button>
                  </div>
                  {files.map((file) => (
                    <FileRow key={`${file.name}-${file.lastModified}`} file={file} onRemove={() => removeFile(file.name, file.lastModified)} />
                  ))}
                </div>
              )}

              {analysis ? (
                <div className="analysis-banner">
                  <span className="analysis-icon"><CheckCircle2 size={18} /></span>
                  <div><strong>Document analysis complete</strong><small>{analysis.total_pages} pages scanned across {files.length} PDF{files.length > 1 ? "s" : ""}</small></div>
                  <button className="icon-button" aria-label="Reanalyze documents" onClick={analyzeFiles}><RefreshCw size={16} /></button>
                </div>
              ) : (
                <button className="button button-primary analyze-button" disabled={!files.length || isAnalyzing} onClick={analyzeFiles}>
                  {isAnalyzing ? <><LoaderCircle className="spin" size={17} /> Analyzing PDFs…</> : <><Sparkles size={17} /> Analyze documents <ArrowRight size={16} /></>}
                </button>
              )}
            </Card>

            {analysis && (
              <Card className="intelligence-card">
                <SectionHeading
                  eyebrow="DOCUMENT INTELLIGENCE · LOCAL PDF ANALYSIS"
                  title="What we found"
                  detail="Transparent document checks and print suggestions; no trained AI model is used."
                  action={analysis.recommendations.length > 0 && (
                    <button className="button button-secondary button-small" onClick={acceptAllSuggestions}>
                      <Sparkles size={14} /> Accept all suggestions
                    </button>
                  )}
                />
                {acceptedSuggestions && <p className="suggestion-confirmed"><CheckCircle2 size={15} /> Suggestions applied to this order’s print setup.</p>}
                <div className="intelligence-files">
                  {analysis.documents.map((document) => {
                    const documentRecommendations = analysis.recommendations.filter((item) => item.filename === document.filename);
                    return (
                      <article className="intelligence-file" key={`${document.filename}-${document.sha256}`}>
                        <div className="intelligence-file-heading"><span className="file-icon"><FileText size={16} /></span><div><strong>{document.filename}</strong><small>{document.category} · {document.total_pages} pages · {document.paper_size} {document.orientation.toLowerCase()}</small></div></div>
                        <div className="intelligence-tags"><span>{document.bw_pages} B&W</span><span>{document.color_pages} color</span><span>{document.blank_pages.length} blank</span></div>
                        {(document.duplicate || document.blank_pages.length > 0 || document.potential_cutoff_pages.length > 0 || document.low_resolution_images.length > 0) && (
                          <ul className="document-warnings">
                            {document.duplicate && <li>Exact duplicate of {document.duplicate_of}; review before printing.</li>}
                            {document.blank_pages.length > 0 && <li>Blank page(s): {document.blank_pages.join(", ")}.</li>}
                            {document.potential_cutoff_pages.length > 0 && <li>Content near page edges on page(s) {document.potential_cutoff_pages.join(", ")}; review for possible cutoff.</li>}
                            {document.low_resolution_images.length > 0 && <li>{document.low_resolution_images.length} large low-resolution image(s) may look soft.</li>}
                          </ul>
                        )}
                        {documentRecommendations.length > 0 && <div className="recommendation-list">{documentRecommendations.map((item, index) => <div key={`${item.type}-${index}`}><strong>{item.title}</strong><span>{item.detail}</span></div>)}</div>}
                        <small className="coverage-disclaimer">Visual page coverage ~{document.estimated_average_page_coverage_percent}%; {document.coverage_note}</small>
                      </article>
                    );
                  })}
                </div>
              </Card>
            )}

            {analysis && (
              <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
                <Card className="preferences-card">
                  <SectionHeading eyebrow="02 / PRINT PREFERENCES" title="Make it yours" detail="One print setup for all selected documents." />
                  <div className="preference-grid">
                    <PreferenceOption selected={!forceBw} icon={FileText} title="As analyzed" detail={`${analysis.bw_pages} B&W · ${analysis.color_pages} color`} onClick={() => { setForceBw(false); setAcceptedSuggestions(false); }} />
                    <PreferenceOption selected={forceBw} icon={SlidersHorizontal} title="All black & white" detail="Save on color printing" onClick={() => { setForceBw(true); setAcceptedSuggestions(false); }} />
                  </div>
                  <div className="preference-divider" />
                  <div className="global-print-settings">
                    <label className="field-label">Paper size<select className="input-control" value={printSettings.paper_size} onChange={(event) => updatePrintSettings({ paper_size: event.target.value })}><option>A4</option><option>A3</option><option>A5</option><option>Letter</option><option>Legal</option></select></label>
                    <label className="field-label">Orientation<select className="input-control" value={printSettings.orientation} onChange={(event) => updatePrintSettings({ orientation: event.target.value })}><option>Portrait</option><option>Landscape</option></select></label>
                    <label className="field-label">Quality<select className="input-control" value={printSettings.quality} onChange={(event) => updatePrintSettings({ quality: event.target.value })}><option>Draft</option><option>Standard</option><option>High</option></select></label>
                    <label className="duplex-control"><input type="checkbox" checked={printSettings.duplex} onChange={(event) => updatePrintSettings({ duplex: event.target.checked })} /><span><strong>Two-sided printing</strong><small>Owner confirms final printer settings.</small></span></label>
                  </div>
                  {analysis.recommendations.some((item) => item.type === "duplex") && <p className="optimizer-note">Before suggestions: {suggestionBaseline?.duplex ?? printSettings.duplex ? "two-sided" : "one-sided"} · {acceptedSuggestions ? "Applied setup: " : "Suggested setup: "}two-sided · About {analysis.recommendations.filter((item) => item.type === "duplex").reduce((sum, item) => sum + (item.paper_sheets_saved || 0), 0)} paper sheet(s) saved. Currency savings are unavailable because paper cost is not configured.</p>}
                  <div className="preference-divider" />
                  <div className="payment-choice">
                    <div><strong>Payment preference</strong><small>Online payment is not configured; payment stays pending.</small></div>
                    <div className="segmented-control">
                      <button type="button" className={paymentMethod === "UPI" ? "selected" : ""} onClick={() => setPaymentMethod("UPI")}><CreditCard size={15} /> UPI at shop</button>
                      <button type="button" className={paymentMethod === "Cash" ? "selected" : ""} onClick={() => setPaymentMethod("Cash")}><ShoppingBag size={15} /> Cash</button>
                    </div>
                  </div>
                </Card>

                <Card className="contact-card">
                  <SectionHeading eyebrow="03 / PICKUP DETAILS" title="Who’s picking this up?" detail="We’ll use these details to identify your order." />
                  <div className="form-grid">
                    <label className="field-label">Full name<input className="input-control" value={customerName} onChange={(event) => setCustomerName(event.target.value)} autoComplete="name" placeholder="Your name" required /></label>
                    <label className="field-label">Phone number<input className="input-control" value={customerPhone} onChange={(event) => setCustomerPhone(event.target.value)} autoComplete="tel" placeholder="+91 98765 43210" required /></label>
                    <label className="field-label field-full">Email <span className="field-optional">optional</span><input className="input-control" type="email" value={customerEmail} onChange={(event) => setCustomerEmail(event.target.value)} autoComplete="email" placeholder="you@example.com" /></label>
                  </div>
                </Card>
              </motion.div>
            )}
          </div>

          <aside className="checkout-column">
            <Card className="shop-card">
              <div className="shop-avatar"><Printer size={19} /></div>
              <div><span className="eyebrow">YOUR PRINT PARTNER</span><h3>{shopName}</h3><p>Local printing, thoughtfully done.</p></div>
              <div className="shop-card-meta"><span><span className="live-dot" /> Accepting orders</span><span><Clock3 size={14} /> Est. 2 hours</span></div>
            </Card>

            <Card className="summary-card">
              <div className="summary-head"><div><span className="eyebrow">ORDER SUMMARY</span><h3>Print checkout</h3></div><span className="summary-bag"><ShoppingBag size={17} /></span></div>
              {analysis ? (
                <>
                  <div className="summary-stats">
                    <div><strong>{analysis.total_pages}</strong><span>Pages</span></div>
                    <div><strong>{files.length}</strong><span>Files</span></div>
                    <div><strong>~2h</strong><span>Ready by</span></div>
                  </div>
                  <div className="charges">
                    <div><span>B&W · {displayedBw} pages</span><span>{money(displayedBw * 2)}</span></div>
                    <div><span>Color · {displayedColor} pages</span><span>{money(displayedColor * 10)}</span></div>
                    <div className="charges-note"><span>Binding / lamination</span><span>Not selected</span></div>
                    <div className="charges-note"><span>GST</span><span>Not included</span></div>
                  </div>
                  <div className="summary-total"><span>Estimated total</span><strong>{money(total)}</strong></div>
                  <p className="summary-footnote">Final charge is calculated by the print shop at the rates shown.</p>
                  <form onSubmit={submitOrder}>
                    <button className="button button-primary checkout-button" type="submit" disabled={isSubmitting}>
                      {isSubmitting ? <><LoaderCircle className="spin" size={17} /> Uploading {uploadProgress}%</> : <>Place print order <ArrowRight size={17} /></>}
                    </button>
                    {isSubmitting && <div className="upload-progress" aria-label={`Upload progress ${uploadProgress}%`}><span style={{ width: `${uploadProgress}%` }} /></div>}
                  </form>
                </>
              ) : (
                <div className="summary-empty"><div className="empty-doc"><FileText size={22} /></div><strong>Your order starts here</strong><p>Add PDFs and analyze them to see page counts and your instant quote.</p></div>
              )}
              <div className="secure-note"><ShieldCheck size={15} /><span>Your files are only used to prepare this print order.</span></div>
            </Card>
            <div className="checkout-help"><MessageCircle size={16} /><span>Questions? Your print shop can help at pickup.</span></div>
          </aside>
        </div>
        <footer className="public-footer"><Brand compact /><span>Thoughtful printing, made simple.</span><Link to="/admin.html">Shop portal <ArrowUpRight size={13} /></Link></footer>
      </main>
    </div>
  );
}

function FileRow({ file, onRemove }: { file: File; onRemove: () => void }) {
  const [preview, setPreview] = useState(false);
  const url = useMemo(() => URL.createObjectURL(file), [file]);
  useEffect(() => () => URL.revokeObjectURL(url), [url]);
  return (
    <div className={`file-item ${preview ? "file-item-preview" : ""}`}>
      <div className="file-icon"><FileText size={18} /></div>
      <button className="file-main" onClick={() => setPreview((value) => !value)} aria-expanded={preview}>
        <strong>{file.name}</strong><small>{(file.size / 1024 / 1024).toFixed(2)} MB <span>·</span> PDF</small>
      </button>
      <span className="file-ready"><Check size={14} /> Ready</span>
      <button className="icon-button file-remove" aria-label={`Remove ${file.name}`} onClick={onRemove}><X size={16} /></button>
      {preview && <iframe className="file-preview" title={`Preview of ${file.name}`} src={url} />}
    </div>
  );
}

function PreferenceOption({ selected, icon: Icon, title, detail, onClick }: { selected: boolean; icon: LucideIcon; title: string; detail: string; onClick: () => void }) {
  return (
    <button className={`preference-option ${selected ? "preference-selected" : ""}`} onClick={onClick} aria-pressed={selected}>
      <span className="option-radio">{selected && <span />}</span>
      <span className="option-icon"><Icon size={17} /></span>
      <span className="option-copy"><strong>{title}</strong><small>{detail}</small></span>
      {selected && <CheckCircle2 className="option-check" size={17} />}
    </button>
  );
}

function TrackingPage({ notify }: { notify: (notice: ToastState | null) => void }) {
  const { orderId = "" } = useParams();
  const trackingToken = new URLSearchParams(window.location.search).get("token") || "";
  const [order, setOrder] = useState<Order | null>(null);
  const [history, setHistory] = useState<Array<{ action: string; status: string; timestamp: string }>>([]);
  const [error, setError] = useState("");
  const cached = useMemo(() => {
    try {
      return JSON.parse(localStorage.getItem(`smartprint_order_${orderId}`) || "{}") as { pin?: string; shopName?: string; customerName?: string };
    } catch {
      return {};
    }
  }, [orderId]);

  const loadOrder = useCallback(async () => {
    try {
      if (!trackingToken) throw new Error("Open the private tracking link from your order confirmation.");
      const trackingQuery = `?tracking_token=${encodeURIComponent(trackingToken)}`;
      const [orderResult, historyResult] = await Promise.all([
        api<ApiResult<Order>>(`/orders/${encodeURIComponent(orderId)}${trackingQuery}`),
        api<ApiResult<Array<{ action: string; status: string; timestamp: string }>>>(`/progress/${encodeURIComponent(orderId)}${trackingQuery}`),
      ]);
      if (!orderResult.data) throw new Error("We couldn’t find this order.");
      if (!historyResult.data) throw new Error("The order history response was incomplete.");
      setOrder(orderResult.data);
      setHistory(historyResult.data);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load this order.");
    }
  }, [orderId, trackingToken]);

  useEffect(() => {
    void loadOrder();
    const timer = window.setInterval(() => void loadOrder(), 10000);
    return () => window.clearInterval(timer);
  }, [loadOrder]);

  const timeline = ["Order received", "Files verified", "Accepted", "Waiting for printing", "Printing", "Quality check", "Ready for pickup", "Completed"];
  const statusToIndex: Record<string, number> = {
    waiting: 3,
    accepted: 2,
    printing: 4,
    "quality check": 5,
    "ready for pickup": 6,
    completed: 7,
    cancelled: 7,
  };
  const activeStep = statusToIndex[(order?.order_status || "waiting").toLowerCase()] ?? 0;
  const qrUrl = `/qr/${encodeURIComponent(orderId)}?tracking_token=${encodeURIComponent(trackingToken)}`;

  const downloadQr = () => {
    const anchor = document.createElement("a");
    anchor.href = qrUrl;
    anchor.download = `smartprint-${orderId}-qr.png`;
    anchor.click();
  };

  const shareOrder = async () => {
    const text = `SmartPrint order ${orderId}${cached.pin ? ` · Pickup PIN ${cached.pin}` : ""}`;
    if (navigator.share) {
      await navigator.share({ title: "SmartPrint order", text, url: window.location.href });
    } else {
      await navigator.clipboard.writeText(`${text} · ${window.location.href}`);
      notify({ tone: "success", message: "Order details copied to clipboard." });
    }
  };

  return (
    <div className="public-page">
      <PublicHeader action="track" />
      <main className="tracking-main">
        {error ? (
          <Card className="tracking-error"><div className="empty-doc"><FileText size={22} /></div><h1>Order not available</h1><p>{error}</p><Link className="button button-primary" to="/">Start a new order</Link></Card>
        ) : !order ? (
          <div className="loading-panel"><LoaderCircle className="spin" /> Loading your order…</div>
        ) : (
          <>
            <div className="tracking-title">
              <span className="eyebrow"><span className="live-dot" /> ORDER TRACKING</span>
              <h1>Your prints are in good hands.</h1>
              <p>Stay in the loop. We’ll update your order here as it moves through the print shop.</p>
            </div>
            <div className="tracking-grid">
              <div className="tracking-main-column">
                <Card className="tracking-status-card">
                  <div className="tracking-status-top">
                    <div><span className="eyebrow">ORDER ID</span><h2>{order.order_id}</h2><p>{cached.shopName || "SmartPrint Studio"} <span>·</span> placed {formatDate(order.created_at)}</p></div>
                    <StatusBadge status={order.order_status} />
                  </div>
                  <div className="tracking-eta">
                    <span className="eta-icon"><Clock3 size={18} /></span>
                    <div><small>ESTIMATED COMPLETION</small><strong>{formatDate(order.estimated_completion)}</strong></div>
                    <span className="eta-note">We’ll keep this page updated</span>
                  </div>
                  <div className="tracking-timeline">
                    {timeline.map((step, index) => {
                      const done = index < activeStep || (index === 0 && history.length > 0);
                      const active = index === activeStep;
                      return (
                        <motion.div key={step} className={`tracking-step ${done ? "step-done" : ""} ${active ? "step-active" : ""}`} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: index * 0.045 }}>
                          <span className="timeline-dot">{done ? <Check size={12} /> : <span />}</span>
                          <div><strong>{step}</strong>{active && <small>Current status</small>}</div>
                        </motion.div>
                      );
                    })}
                  </div>
                  {history.length > 0 && <div className="latest-update"><Activity size={15} /><span>Latest update: {history[history.length - 1].action} · {formatDate(history[history.length - 1].timestamp)}</span></div>}
                </Card>
                <Card className="tracking-order-card">
                  <SectionHeading eyebrow="YOUR ORDER" title="Print details" detail="A quick summary of what’s being prepared." />
                  <div className="tracking-file-row"><span className="file-icon"><FileText size={18} /></span><div><strong>{order.filename}</strong><small>{order.total_pages} pages · PDF</small></div><span className="file-total">{money(order.total_price)}</span></div>
                  <div className="tracking-page-tags"><span>{order.bw_pages} B&W pages</span><span>{order.color_pages} color pages</span></div>
                  <div className="tracking-payment"><span><CreditCard size={16} /> Payment status</span><StatusBadge status={order.payment_status || "Pending"} /><strong>{money(order.total_price)}</strong></div>
                </Card>
              </div>

              <aside className="tracking-aside">
                <Card className="pickup-card">
                  <div className="pickup-card-top"><span className="eyebrow">READY WHEN YOU ARE</span><QrCode size={18} /></div>
                  <h2>Pickup pass</h2>
                  <p>Show this QR code or your PIN at the counter when your order is ready.</p>
                  <div className="pickup-qr"><img src={qrUrl} alt={`Pickup QR code for order ${order.order_id}`} onError={(event) => { event.currentTarget.style.display = "none"; }} /><QrCode className="qr-fallback" size={52} /></div>
                  <div className="pickup-pin"><small>PICKUP PIN</small><strong>{cached.pin || "••••"}</strong><span>{cached.pin ? "Keep this code private" : "Use the QR code on arrival"}</span></div>
                  <div className="pickup-actions">
                    <button className="button button-secondary" onClick={downloadQr}><ArrowDownToLine size={16} /> Download</button>
                    <button className="button button-secondary" onClick={() => void shareOrder()}><Share2 size={16} /> Share</button>
                  </div>
                  <button className="text-button pickup-print" onClick={() => window.print()}><Printer size={14} /> Print pickup pass</button>
                </Card>
                <div className="tracking-help"><ShieldCheck size={17} /><div><strong>Order securely tracked</strong><span>Refreshes automatically every 10 seconds.</span></div></div>
              </aside>
            </div>
          </>
        )}
      </main>
    </div>
  );
}

function OwnerPage({ notify }: { notify: (notice: ToastState | null) => void }) {
  const navigate = useNavigate();
  const [user, setUser] = useState<ShopUser | null>(null);
  const [sessionLoading, setSessionLoading] = useState(true);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [mobileNav, setMobileNav] = useState(false);
  const [section, setSection] = useState("overview");
  const [orders, setOrders] = useState<Order[]>([]);
  const [printers, setPrinters] = useState<PrinterDevice[]>([]);
  const [inventory, setInventory] = useState<InventoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [drawerOrder, setDrawerOrder] = useState<Order | null>(null);
  const [orderSearch, setOrderSearch] = useState("");
  const [orderFilter, setOrderFilter] = useState("All orders");
  const [sortNewest, setSortNewest] = useState(true);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [authMessage, setAuthMessage] = useState("");
  const [notifications, setNotifications] = useState<Array<{ notification_id: number; message: string; created_at: string }>>([]);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [searchPhone, setSearchPhone] = useState("");
  const [customerResults, setCustomerResults] = useState<Order[]>([]);
  const [reportRows, setReportRows] = useState<Array<Record<string, string | number>>>([]);
  const [addPrinterOpen, setAddPrinterOpen] = useState(false);
  const [branchId, setBranchId] = useState("1");
  const [newPrinterName, setNewPrinterName] = useState("");
  const [newPrinterModel, setNewPrinterModel] = useState("");
  const [updatingStock, setUpdatingStock] = useState<number | null>(null);

  useEffect(() => {
    let active = true;
    void api<ApiResult<ShopUser>>("/session/")
      .then((result) => {
        if (!result.data) throw new Error("The owner session response was incomplete.");
        if (active) {
          setUser(result.data);
          localStorage.setItem("smartprint_owner_id", String(result.data.owner_id));
          localStorage.setItem("smartprint_shop_name", result.data.shop_name);
        }
      })
      .catch((error) => {
        if (!active) return;
        setUser(null);
        if (!(error instanceof Error && error.message.includes("Owner authentication is required"))) {
          setAuthMessage(error instanceof Error ? error.message : "Could not verify the owner session.");
        }
      })
      .finally(() => { if (active) setSessionLoading(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!user) return;
    let active = true;
    const load = async () => {
      setLoading(true);
      try {
        const [orderResult, printerResult, inventoryResult] = await Promise.all([
          api<ApiResult<Order[]>>("/orders/"),
          api<ApiResult<PrinterDevice[]>>("/printers/status"),
          api<ApiResult<InventoryItem[]>>("/inventory/"),
        ]);
        if (!active) return;
        if (!orderResult.data || !printerResult.data || !inventoryResult.data) {
          throw new Error("The dashboard response was incomplete.");
        }
        setOrders(orderResult.data.filter((order) => Number(order.owner_id) === user.owner_id));
        setPrinters(printerResult.data);
        setInventory(inventoryResult.data);
      } catch (error) {
        if (active) notify({ tone: "error", message: error instanceof Error ? error.message : "Could not load the dashboard." });
      } finally {
        if (active) setLoading(false);
      }
    };
    void load();
    const timer = window.setInterval(() => setRefreshKey((key) => key + 1), 15000);
    return () => { active = false; window.clearInterval(timer); };
  }, [user, refreshKey, notify]);

  useEffect(() => {
    if (!user) return;
    void api<ApiResult<Array<{ notification_id: number; message: string; created_at: string }>>>(`/notifications/owner/${user.owner_id}`)
      .then((result) => {
        if (!result.data) throw new Error("The notifications response was incomplete.");
        setNotifications(result.data);
      })
      .catch((error) => notify({ tone: "error", message: error instanceof Error ? error.message : "Could not load notifications." }));
  }, [user, notify]);

  const handleAuth = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const path = mode === "login" ? "/login/" : "/register/";
      const result = await api<{ status: string; message?: string; owner_id?: number; shop_name?: string }>(path, { method: "POST", body: form });
      if (mode === "register") {
        setAuthMessage(result.message || "Shop registered. Sign in to continue.");
        setMode("login");
      } else {
        if (!result.owner_id || !result.shop_name) throw new Error("The login response was missing shop information.");
        localStorage.setItem("smartprint_owner_id", String(result.owner_id));
        localStorage.setItem("smartprint_shop_name", result.shop_name);
        setUser({ owner_id: result.owner_id, shop_name: result.shop_name });
        setAuthMessage("");
        navigate("/dashboard/");
      }
    } catch (error) {
      setAuthMessage(error instanceof Error ? error.message : "Unable to authenticate.");
    }
  };

  const logout = async () => {
    try {
      await api("/logout/", { method: "POST" });
    } catch (error) {
      notify({ tone: "error", message: error instanceof Error ? error.message : "Could not sign out." });
      return;
    }
    localStorage.removeItem("smartprint_owner_id");
    localStorage.removeItem("smartprint_shop_name");
    setUser(null);
    navigate("/admin.html");
  };

  const selectSection = (nextSection: string) => {
    setSection(nextSection);
    setMobileNav(false);
    setSelectedIds([]);
  };

  const filteredOrders = useMemo(() => {
    const search = orderSearch.trim().toLowerCase();
    return [...orders]
      .filter((order) => orderFilter === "All orders" || order.order_status === orderFilter)
      .filter((order) => !search || [order.order_id, order.filename, String(order.customer_id || ""), order.payment_status].some((value) => value.toLowerCase().includes(search)))
      .sort((a, b) => sortNewest ? b.order_id.localeCompare(a.order_id) : a.order_id.localeCompare(b.order_id));
  }, [orderFilter, orderSearch, orders, sortNewest]);

  const today = new Date().toDateString();
  const todayOrders = orders.filter((order) => new Date(order.created_at).toDateString() === today);
  const revenueToday = todayOrders
    .filter((order) => order.order_status.toLowerCase() === "completed")
    .reduce((sum, order) => sum + Number(order.total_price || 0), 0);
  const pendingCount = orders.filter((order) => ["waiting", "pending"].includes(order.order_status.toLowerCase())).length;
  const completedCount = orders.filter((order) => order.order_status.toLowerCase() === "completed").length;
  const monthRevenue = orders.filter((order) => {
    const date = new Date(order.created_at);
    return date.getMonth() === new Date().getMonth() && date.getFullYear() === new Date().getFullYear();
  }).filter((order) => order.order_status.toLowerCase() === "completed")
    .reduce((sum, order) => sum + Number(order.total_price || 0), 0);

  const submitMerge = async () => {
    if (!selectedIds.length) {
      notify({ tone: "error", message: "Select one or more orders to prepare a batch." });
      return;
    }
    try {
      const result = await api<{ status: string; urls: string[] }>("/merge/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ order_ids: selectedIds }),
      });
      result.urls.forEach((url) => window.open(url, "_blank", "noopener,noreferrer"));
      if (!result.urls.length) notify({ tone: "info", message: "No printable pages were returned for this batch." });
      else notify({ tone: "success", message: "Print-ready batch files are open in new tabs." });
    } catch (error) {
      notify({ tone: "error", message: error instanceof Error ? error.message : "Could not prepare the batch." });
    }
  };

  const completeOrder = async (order: Order) => {
    if (!window.confirm(`Mark order ${order.order_id} completed? Confirm only after the owner has verified printing or pickup. Original files will be retained for smart reprints.`)) return;
    try {
      await api(`/orders/${encodeURIComponent(order.order_id)}`, { method: "DELETE" });
      setOrders((current) => current.map((item) => item.order_id === order.order_id ? { ...item, order_status: "Completed" } : item));
      setDrawerOrder(null);
      notify({ tone: "success", message: "Order marked completed. The original documents remain available for reprints." });
    } catch (error) {
      notify({ tone: "error", message: error instanceof Error ? error.message : "Could not complete the order." });
    }
  };

  const lookupCustomer = async (event: FormEvent) => {
    event.preventDefault();
    if (!searchPhone.trim()) return;
    try {
      const result = await api<ApiResult<Order[]>>(`/search/orders/phone/${encodeURIComponent(searchPhone.trim())}?owner_id=${user?.owner_id}`);
      if (!result.data) throw new Error("The customer search response was incomplete.");
      setCustomerResults(result.data);
      if (!result.data.length) notify({ tone: "info", message: "No orders were found for that phone number." });
    } catch (error) {
      notify({ tone: "error", message: error instanceof Error ? error.message : "Customer search failed." });
    }
  };

  const loadReports = async () => {
    try {
      const result = await api<ApiResult<Array<Record<string, string | number>>>>("/analytics/revenue?period=monthly");
      if (!result.data) throw new Error("The report response was incomplete.");
      setReportRows(result.data);
    } catch (error) {
      notify({ tone: "error", message: error instanceof Error ? error.message : "Could not load report data." });
    }
  };

  const saveInventory = async (item: InventoryItem, stock: number) => {
    setUpdatingStock(item.inventory_id);
    const form = new FormData();
    form.append("inventory_id", String(item.inventory_id));
    form.append("current_stock", String(stock));
    try {
      await api("/inventory/", { method: "POST", body: form });
      setInventory((current) => current.map((entry) => entry.inventory_id === item.inventory_id ? { ...entry, current_stock: stock } : entry));
      notify({ tone: "success", message: `${item.item_name} stock updated.` });
    } catch (error) {
      notify({ tone: "error", message: error instanceof Error ? error.message : "Could not update stock." });
    } finally {
      setUpdatingStock(null);
    }
  };

  const savePrinter = async (event: FormEvent) => {
    event.preventDefault();
    const form = new FormData();
    form.append("printer_name", newPrinterName);
    form.append("model", newPrinterModel);
    form.append("branch_id", branchId);
    try {
      await api("/printers/", { method: "POST", body: form });
      setAddPrinterOpen(false);
      setNewPrinterName("");
      setNewPrinterModel("");
      setRefreshKey((key) => key + 1);
      notify({ tone: "success", message: "Printer added to your shop." });
    } catch (error) {
      notify({ tone: "error", message: error instanceof Error ? error.message : "Could not add printer." });
    }
  };

  useEffect(() => {
    if (section === "reports") void loadReports();
    // Load only when switching to this screen.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [section]);

  if (sessionLoading) {
    return <div className="loading-panel"><LoaderCircle className="spin" /> Verifying your shop session…</div>;
  }

  if (!user) {
    return (
      <div className="auth-page">
        <PublicHeader />
        <main className="auth-layout">
          <div className="auth-story">
            <span className="eyebrow"><span className="live-dot" /> SMARTPRINT FOR BUSINESS</span>
            <h1>Your print shop,<br /><span>in perfect flow.</span></h1>
            <p>Stay on top of every order, printer and page — all from one calm, clear workspace.</p>
            <div className="auth-benefits"><span><CheckCircle2 size={17} /> One queue for every order</span><span><CheckCircle2 size={17} /> Print-ready batch PDFs</span><span><CheckCircle2 size={17} /> Insights at a glance</span></div>
            <div className="auth-visual"><div className="auth-visual-lines"><span /><span /><span /></div><div className="auth-visual-total"><small>TODAY'S PRINT SALES</small><strong>₹12,480</strong><span><TrendingUp size={14} /> +18.4% this week</span></div><div className="auth-visual-ring"><Printer size={22} /></div></div>
          </div>
          <Card className="auth-card">
            <div className="auth-card-brand"><Brand compact /><span>SHOP PORTAL</span></div>
            <span className="eyebrow">{mode === "login" ? "WELCOME BACK" : "GET STARTED"}</span>
            <h2>{mode === "login" ? "Sign in to your shop" : "Create your shop account"}</h2>
            <p className="auth-subtitle">{mode === "login" ? "Pick up right where your team left off." : "Set up your print shop workspace in a moment."}</p>
            <form className="auth-form" onSubmit={handleAuth}>
              {mode === "register" && <label className="field-label">Shop name<input className="input-control" name="shop_name" placeholder="e.g. Campus Print Studio" required /></label>}
              <label className="field-label">Username<input className="input-control" name="username" autoComplete="username" placeholder="Your username" required /></label>
              <label className="field-label">Password<input className="input-control" name="password" autoComplete={mode === "login" ? "current-password" : "new-password"} type="password" placeholder="Enter your password" required /></label>
              {authMessage && <p className="form-message" role="alert">{authMessage}</p>}
              <button className="button button-primary auth-submit" type="submit">{mode === "login" ? "Sign in" : "Create account"} <ArrowRight size={17} /></button>
            </form>
            <p className="auth-toggle">{mode === "login" ? "New to SmartPrint?" : "Already have an account?"} <button className="text-button" onClick={() => { setMode(mode === "login" ? "register" : "login"); setAuthMessage(""); }}>{mode === "login" ? "Create a shop" : "Sign in"}</button></p>
            <div className="auth-security"><ShieldCheck size={15} /> Your shop workspace is protected.</div>
          </Card>
        </main>
      </div>
    );
  }

  const pageTitles: Record<string, { title: string; subtitle: string }> = {
    overview: { title: "Good morning", subtitle: "Here’s what’s happening at your shop today." },
    orders: { title: "Orders", subtitle: "Review incoming print jobs and keep work moving." },
    batch: { title: "Batch printing", subtitle: "Group selected orders into printer-ready PDFs." },
    queue: { title: "Print queue", subtitle: "A focused view of orders waiting for the printer." },
    customers: { title: "Customers", subtitle: "Look up customer orders by their phone number." },
    history: { title: "Print history", subtitle: "Review orders and their current processing status." },
    reprints: { title: "Smart reprint", subtitle: "Find a previous order, restore its print setup and reprice it." },
    profiles: { title: "Print profiles", subtitle: "Save repeatable printer, paper and quality settings." },
    inventory: { title: "Inventory", subtitle: "Keep supplies visible and update stock as you restock." },
    printers: { title: "Printers", subtitle: "Check printer availability across your branches." },
    reports: { title: "Reports", subtitle: "A monthly snapshot of completed print revenue." },
    settings: { title: "Shop settings", subtitle: "Share your print link and manage your shop workspace." },
  };
  const title = pageTitles[section];

  return (
    <div className="owner-layout">
      <aside className={`owner-sidebar ${mobileNav ? "sidebar-open" : ""}`}>
        <div className="sidebar-brand"><Brand /><button className="icon-button sidebar-close" aria-label="Close navigation" onClick={() => setMobileNav(false)}><X size={18} /></button></div>
        <div className="sidebar-shop"><span className="shop-monogram">{user.shop_name.slice(0, 1).toUpperCase()}</span><div><strong>{user.shop_name}</strong><small>Print shop workspace</small></div><ChevronDown size={15} /></div>
        <span className="sidebar-label">WORKSPACE</span>
        <nav className="sidebar-nav" aria-label="Dashboard navigation">
          {navItems.map(({ label, icon: Icon, section: id }) => (
            <button key={id} className={`nav-item ${section === id ? "nav-item-active" : ""}`} onClick={() => selectSection(id)} aria-current={section === id ? "page" : undefined}>
              <Icon size={17} /><span>{label}</span>{id === "orders" && pendingCount > 0 && <span className="nav-count">{pendingCount}</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-tip"><Sparkles size={16} /><div><strong>Print tip</strong><span>Use batch print to separate color and B&W pages.</span></div></div>
          <button className="sidebar-logout" onClick={logout}><LogOut size={16} /> Sign out</button>
        </div>
      </aside>
      {mobileNav && <button className="sidebar-backdrop" aria-label="Close navigation" onClick={() => setMobileNav(false)} />}
      <div className="owner-content">
        <header className="owner-topbar">
          <div className="owner-topbar-left"><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setMobileNav(true)}><Menu size={19} /></button><div className="breadcrumb"><span>Workspace</span><ChevronRight size={13} /><strong>{title.title}</strong></div></div>
          <div className="owner-top-actions"><button className="icon-button notification-button" aria-label={`${notifications.length} notifications`} aria-expanded={notificationsOpen} title={`${notifications.length} notifications`} onClick={() => setNotificationsOpen((open) => !open)}><Bell size={17} />{notifications.length > 0 && <span className="notification-dot" />}</button>{notificationsOpen && <div className="notification-popover"><div className="notification-popover-head"><strong>Notifications</strong><span>{notifications.length}</span></div>{notifications.length ? notifications.slice(0, 8).map((notification) => <div className="notification-item" key={notification.notification_id}><span className="notification-icon"><Bell size={14} /></span><div><p>{notification.message}</p><small>{formatDate(notification.created_at)}</small></div></div>) : <div className="notification-empty"><Bell size={18} /><span>You’re all caught up.</span></div>}</div>}<span className="topbar-separator" /><span className="avatar">{user.shop_name.slice(0, 1).toUpperCase()}</span><div className="topbar-user"><strong>{user.shop_name}</strong><small>Shop owner</small></div></div>
        </header>
        <main className="owner-main">
          <div className="dashboard-heading">
            <div><span className="eyebrow">{new Intl.DateTimeFormat("en-IN", { weekday: "long", day: "numeric", month: "long" }).format(new Date())}</span><h1>{title.title}{section === "overview" && <span>, {user.shop_name.split(" ")[0]}.</span>}</h1><p>{title.subtitle}</p></div>
            <div className="heading-actions"><button className="button button-secondary" onClick={() => setRefreshKey((key) => key + 1)}><RefreshCw size={15} /> Refresh</button><button className="button button-primary" onClick={() => navigate("/") }><FilePlus2 size={16} /> New order</button></div>
          </div>

          {section === "overview" && (
            <OverviewSection orders={orders} printers={printers} revenueToday={revenueToday} monthRevenue={monthRevenue} pendingCount={pendingCount} completedCount={completedCount} loading={loading} onOrders={() => selectSection("orders")} onOrder={setDrawerOrder} />
          )}
          {(section === "orders" || section === "history" || section === "queue" || section === "batch") && (
            <OrdersSection
              section={section}
              orders={section === "queue" ? filteredOrders.filter((order) => ["waiting", "pending", "accepted", "printing"].includes(order.order_status.toLowerCase())) : section === "history" ? filteredOrders.filter((order) => ["completed", "cancelled"].includes(order.order_status.toLowerCase())) : filteredOrders}
              search={orderSearch} onSearch={setOrderSearch} filter={orderFilter} onFilter={setOrderFilter}
              sortNewest={sortNewest} onSort={() => setSortNewest((value) => !value)}
              selectedIds={selectedIds} onSelect={setSelectedIds} onMerge={submitMerge} onOrder={setDrawerOrder}
              onComplete={completeOrder} loading={loading}
            />
          )}
          {section === "customers" && (
            <CustomersSection searchPhone={searchPhone} setSearchPhone={setSearchPhone} results={customerResults} onSubmit={lookupCustomer} onOrder={setDrawerOrder} />
          )}
          {section === "reprints" && <SmartReprintSection ownerId={user.owner_id} notify={notify} onCreated={() => setRefreshKey((key) => key + 1)} />}
          {section === "profiles" && <PrintingProfilesSection ownerId={user.owner_id} printers={printers} notify={notify} />}
          {section === "inventory" && <InventorySection inventory={inventory} onSave={saveInventory} updatingStock={updatingStock} />}
          {section === "printers" && <PrintersSection printers={printers} onAdd={() => setAddPrinterOpen(true)} />}
          {section === "reports" && <ReportsSection rows={reportRows} />}
          {section === "settings" && <SettingsSection shopName={user.shop_name} ownerId={user.owner_id} notify={notify} />}
        </main>
        <nav className="mobile-bottom-nav" aria-label="Mobile dashboard navigation">
          {navItems.slice(0, 4).map(({ label, icon: Icon, section: id }) => <button key={id} className={section === id ? "mobile-nav-active" : ""} onClick={() => selectSection(id)}><Icon size={18} /><span>{label === "Batch printing" ? "Batch" : label}</span></button>)}
          <button onClick={() => setMobileNav(true)}><MoreHorizontal size={19} /><span>More</span></button>
        </nav>
      </div>
      <AnimatePresence>
        {drawerOrder && <OrderDrawer order={drawerOrder} ownerId={user.owner_id} onClose={() => setDrawerOrder(null)} onComplete={() => void completeOrder(drawerOrder)} notify={notify} />}
        {addPrinterOpen && (
          <Modal title="Add a printer" onClose={() => setAddPrinterOpen(false)}>
            <form className="modal-form" onSubmit={(event) => void savePrinter(event)}>
              <label className="field-label">Printer name<input className="input-control" value={newPrinterName} onChange={(event) => setNewPrinterName(event.target.value)} required placeholder="e.g. Front desk printer" /></label>
              <label className="field-label">Model <span className="field-optional">optional</span><input className="input-control" value={newPrinterModel} onChange={(event) => setNewPrinterModel(event.target.value)} placeholder="Manufacturer / model" /></label>
              <label className="field-label">Branch ID<input className="input-control" type="number" min="1" value={branchId} onChange={(event) => setBranchId(event.target.value)} required /></label>
              <button className="button button-primary" type="submit">Add printer <ArrowRight size={16} /></button>
            </form>
          </Modal>
        )}
      </AnimatePresence>
    </div>
  );
}

function OverviewSection({ orders, printers, revenueToday, monthRevenue, pendingCount, completedCount, loading, onOrders, onOrder }: {
  orders: Order[]; printers: PrinterDevice[]; revenueToday: number; monthRevenue: number; pendingCount: number; completedCount: number;
  loading: boolean; onOrders: () => void; onOrder: (order: Order) => void;
}) {
  const metrics = [
    { label: "Today's revenue", value: money(revenueToday), detail: `${orders.filter((order) => new Date(order.created_at).toDateString() === new Date().toDateString()).length} orders today`, icon: CreditCard, tone: "teal" },
    { label: "Total orders", value: String(orders.length), detail: "Across all order statuses", icon: ShoppingBag, tone: "purple" },
    { label: "Pending orders", value: String(pendingCount), detail: "Need shop attention", icon: Clock3, tone: "orange" },
    { label: "Completed orders", value: String(completedCount), detail: "Ready and fulfilled", icon: CheckCircle2, tone: "green" },
  ];
  return (
    <div className="dashboard-section">
      <div className="metric-grid">
        {metrics.map(({ label, value, detail, icon: Icon, tone }, index) => (
          <motion.div key={label} className={`metric-card metric-${tone}`} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: index * 0.055 }}>
            <div className="metric-top"><span>{label}</span><span className="metric-icon"><Icon size={17} /></span></div>
            <strong className="metric-value">{loading ? <span className="skeleton skeleton-number" /> : value}</strong>
            <small>{detail}</small>
          </motion.div>
        ))}
      </div>
      <div className="overview-grid">
        <Card className="revenue-card">
          <div className="card-title-row"><div><span className="eyebrow">REVENUE</span><h2>{money(monthRevenue)}</h2><p>Order value this month</p></div><span className="trend-chip"><TrendingUp size={14} /> Month to date</span></div>
          <div className="revenue-visual"><RevenueSparkline orders={orders} /></div>
          <div className="revenue-foot"><span><i className="legend-dot teal-dot" /> Orders this month</span><button className="text-button" onClick={onOrders}>Explore orders <ArrowRight size={14} /></button></div>
        </Card>
        <Card className="printer-summary">
          <div className="card-title-row"><div><span className="eyebrow">FLEET HEALTH</span><h3>Printer status</h3></div><span className="metric-icon icon-purple"><Printer size={17} /></span></div>
          <div className="printer-total"><strong>{printers.filter((item) => item.status.toLowerCase() === "online").length}</strong><span>of {printers.length} online</span></div>
          <div className="printer-status-track"><span style={{ width: `${printers.length ? printers.filter((item) => item.status.toLowerCase() === "online").length / printers.length * 100 : 0}%` }} /></div>
          <div className="printer-mini-list">{printers.slice(0, 3).map((printer) => <div key={printer.printer_id}><span className={`printer-indicator ${printer.status.toLowerCase() === "online" ? "is-online" : ""}`} /><span>{printer.printer_name}</span><small>{printer.status}</small></div>)}{printers.length === 0 && <p className="muted-copy">No printers have been added yet.</p>}</div>
        </Card>
      </div>
      <Card className="orders-table-card">
        <SectionHeading eyebrow="LIVE WORKSPACE" title="Recent orders" detail="The latest print jobs across your shop." action={<button className="button button-secondary button-small" onClick={onOrders}>View all <ArrowRight size={14} /></button>} />
        <OrdersTable orders={orders.slice(0, 6)} onOrder={onOrder} />
      </Card>
    </div>
  );
}

function RevenueSparkline({ orders }: { orders: Order[] }) {
  const days = Array.from({ length: 7 }, (_, index) => {
    const date = new Date();
    date.setDate(date.getDate() - (6 - index));
    const amount = orders.filter((order) => new Date(order.created_at).toDateString() === date.toDateString())
      .reduce((sum, order) => sum + Number(order.total_price || 0), 0);
    return { day: new Intl.DateTimeFormat("en-IN", { weekday: "short" }).format(date), amount };
  });
  const max = Math.max(...days.map((day) => day.amount), 1);
  const points = days.map((item, index) => `${index * 100},${94 - item.amount / max * 66}`).join(" ");
  return (
    <div className="spark-chart">
      <svg viewBox="0 0 600 110" preserveAspectRatio="none" role="img" aria-label="Revenue trend over the last seven days">
        <defs><linearGradient id="spark-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#00d6c0" stopOpacity=".24" /><stop offset="100%" stopColor="#00d6c0" stopOpacity="0" /></linearGradient></defs>
        <polygon points={`0,110 ${points} 600,110`} fill="url(#spark-fill)" />
        <polyline points={points} fill="none" stroke="#00d6c0" strokeWidth="2.5" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="spark-labels">{days.map((item) => <span key={item.day}>{item.day}</span>)}</div>
    </div>
  );
}

function OrdersSection(props: {
  section: string; orders: Order[]; search: string; onSearch: (value: string) => void; filter: string; onFilter: (value: string) => void;
  sortNewest: boolean; onSort: () => void; selectedIds: string[]; onSelect: (ids: string[]) => void; onMerge: () => void;
  onOrder: (order: Order) => void; onComplete: (order: Order) => void; loading: boolean;
}) {
  const { section, orders, search, onSearch, filter, onFilter, sortNewest, onSort, selectedIds, onSelect, onMerge, onOrder, onComplete, loading } = props;
  const batch = section === "batch";
  return (
    <div className="dashboard-section">
      {batch && <Card className="batch-intro"><div className="batch-intro-icon"><FileCheck2 size={22} /></div><div><span className="eyebrow">GLOBAL PRINT SETTINGS</span><h2>Configure once. Print the batch.</h2><p>Select queued orders, then prepare separate B&W and color master PDFs for your print run.</p></div><div className="batch-pill"><CheckCircle2 size={15} /> Automatic color separation</div></Card>}
      <Card className="orders-table-card orders-page-card">
        <div className="orders-tools">
          <label className="search-control"><Search size={16} /><input value={search} onChange={(event) => onSearch(event.target.value)} placeholder="Search order, file or customer ID…" aria-label="Search orders" /></label>
          <div className="orders-tool-actions">
            <label className="select-control"><Filter size={15} /><select value={filter} onChange={(event) => onFilter(event.target.value)} aria-label="Filter by status"><option>All orders</option>{["Waiting", "Accepted", "Printing", "Ready for Pickup", "Completed", "Cancelled"].map((status) => <option key={status}>{status}</option>)}</select><ChevronDown size={14} /></label>
            <button className="button button-secondary button-small" onClick={onSort}><ArrowDown size={14} /> {sortNewest ? "Newest" : "Oldest"}</button>
          </div>
        </div>
        {batch && <div className="batch-toolbar"><label className="select-all"><input type="checkbox" checked={orders.length > 0 && selectedIds.length === orders.length} onChange={(event) => onSelect(event.target.checked ? orders.map((order) => order.order_id) : [])} /> Select all visible <span>{selectedIds.length ? `${selectedIds.length} selected` : ""}</span></label><button className="button button-primary button-small" disabled={!selectedIds.length} onClick={onMerge}><Printer size={15} /> Prepare batch <ArrowRight size={14} /></button></div>}
        {loading && orders.length === 0 ? <LoadingRows /> : <OrdersTable orders={orders} onOrder={onOrder} selectedIds={batch ? selectedIds : undefined} onSelect={onSelect} onComplete={onComplete} />}
      </Card>
      {(section === "queue" || batch) && <div className="queue-note"><ShieldCheck size={16} /><span>Completion is owner-confirmed; SmartPrint does not detect or claim that a printer job has finished.</span></div>}
    </div>
  );
}

function OrdersTable({ orders, onOrder, selectedIds, onSelect, onComplete }: {
  orders: Order[]; onOrder: (order: Order) => void; selectedIds?: string[]; onSelect?: (ids: string[]) => void; onComplete?: (order: Order) => void;
}) {
  if (orders.length === 0) return <div className="table-empty"><div className="empty-doc"><FileText size={21} /></div><strong>No orders to show</strong><p>New print jobs will appear here as soon as they’re submitted.</p></div>;
  return (
    <div className="table-scroll"><table className="orders-table"><thead><tr>{selectedIds && <th className="checkbox-cell"><span className="sr-only">Select</span></th>}<th>Order</th><th>Document</th><th>Pages</th><th>Amount</th><th>Payment</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead>
      <tbody>{orders.map((order) => <tr key={order.order_id}>
        {selectedIds && <td className="checkbox-cell"><input type="checkbox" aria-label={`Select order ${order.order_id}`} checked={selectedIds.includes(order.order_id)} onChange={(event) => onSelect?.(event.target.checked ? [...selectedIds, order.order_id] : selectedIds.filter((id) => id !== order.order_id))} /></td>}
        <td><button className="order-id-button" onClick={() => onOrder(order)}>{order.order_id}<small>{formatDate(order.created_at)}</small></button></td>
        <td><div className="document-cell"><span className="tiny-file"><FileText size={15} /></span><span title={order.filename}>{order.filename}</span></div></td>
        <td><strong className="page-number">{order.total_pages}</strong><small className="page-breakdown">{order.bw_pages} B&W / {order.color_pages} color</small></td>
        <td className="amount-cell">{money(order.total_price)}</td>
        <td><span className={`payment-status ${order.payment_status?.toLowerCase() === "paid" ? "payment-paid" : ""}`}>{order.payment_status || "Pending"}</span></td>
        <td><StatusBadge status={order.order_status || "Waiting"} /></td>
        <td><div className="table-row-actions"><button className="icon-button" aria-label={`View ${order.order_id}`} onClick={() => onOrder(order)}><ChevronRight size={17} /></button>{onComplete && <button className="icon-button row-complete" aria-label={`Complete ${order.order_id}`} title="Complete and remove order" onClick={() => onComplete(order)}><Check size={16} /></button>}</div></td>
      </tr>)}</tbody>
    </table></div>
  );
}

function SmartReprintSection({ ownerId, notify, onCreated }: {
  ownerId: number;
  notify: (notice: ToastState | null) => void;
  onCreated: () => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ReprintSearchItem[]>([]);
  const [profiles, setProfiles] = useState<PrintingProfile[]>([]);
  const [profileId, setProfileId] = useState("");
  const [loading, setLoading] = useState(false);
  const [activeOrder, setActiveOrder] = useState<string | null>(null);
  const [newReprint, setNewReprint] = useState<{ order_id: string; tracking_token: string; pickup_pin: string; total_price: number } | null>(null);

  const search = async (event?: FormEvent) => {
    event?.preventDefault();
    setLoading(true);
    try {
      const result = await api<ApiResult<ReprintSearchItem[]>>(
        `/reprints/search?owner_id=${ownerId}&q=${encodeURIComponent(query.trim())}`,
      );
      if (!result.data) throw new Error("The reprint search response was incomplete.");
      setResults(result.data);
    } catch (error) {
      notify({ tone: "error", message: error instanceof Error ? error.message : "Could not search previous orders." });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void search(); }, [ownerId]);
  useEffect(() => {
    void api<ApiResult<PrintingProfile[]>>(`/profiles/?owner_id=${ownerId}`)
      .then((result) => {
        if (!result.data) throw new Error("The printing profiles response was incomplete.");
        setProfiles(result.data);
      })
      .catch((error) => notify({ tone: "error", message: error instanceof Error ? error.message : "Could not load print profiles." }));
  }, [ownerId, notify]);

  const reprint = async (order: ReprintSearchItem) => {
    setActiveOrder(order.order_id);
    setNewReprint(null);
    try {
      const details = await api<ApiResult<{
        order: ReprintSearchItem;
        documents: Array<{ original_filename: string }>;
        settings: PrintingProfile;
        settings_available: boolean;
        source_files_available: boolean;
        estimated_total_price: number;
        estimated_total_pages: number;
      }>>(`/orders/${encodeURIComponent(order.order_id)}/reprint?owner_id=${ownerId}${profileId ? `&profile_id=${profileId}` : ""}`);
      const data = details.data;
      if (!data) throw new Error("The reprint details response was incomplete.");
      if (!data.source_files_available) {
        notify({ tone: "error", message: "The original document is no longer available for reprinting." });
        return;
      }
      const selectedProfile = profiles.find((profile) => String(profile.profile_id) === profileId);
      const settings = `${selectedProfile ? `${selectedProfile.profile_name}: ` : data.settings_available ? "Saved setup: " : "Default setup (original setup not saved): "}${data.settings.paper_size}, ${data.settings.orientation}, ${data.settings.color_mode}, ${data.settings.duplex ? "duplex" : "single-sided"}, ${data.settings.copies} copy/copies`;
      if (!window.confirm(
        `Create a new order from ${order.order_id}?\\n\\nDocuments: ${data.documents.map((item) => item.original_filename).join(", ")}\\nSettings: ${settings}\\nEstimated current price: ${money(data.estimated_total_price)}\\n\\nThe new order will be pending; no payment or printer job will be started.`,
      )) return;
      const created = await api<{
        status: string;
        order_id: string;
        tracking_token: string;
        pickup_pin: string;
        total_price: number;
      }>(`/orders/${encodeURIComponent(order.order_id)}/reprint`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ owner_id: ownerId, profile_id: profileId ? Number(profileId) : null }),
      });
      setNewReprint({
        order_id: created.order_id,
        tracking_token: created.tracking_token,
        pickup_pin: created.pickup_pin,
        total_price: created.total_price,
      });
      notify({ tone: "success", message: `Reprint order ${created.order_id} was created at ${money(created.total_price)}.` });
      onCreated();
      await search();
    } catch (error) {
      notify({ tone: "error", message: error instanceof Error ? error.message : "Could not create the reprint." });
    } finally {
      setActiveOrder(null);
    }
  };

  return (
    <div className="dashboard-section">
      <Card className="orders-table-card">
        <SectionHeading eyebrow="ORIGINAL FILES + SAVED SETTINGS" title="Find an order to reprint" detail="Reprinting creates a new pending order, copies the source documents and recalculates at current page rates." />
        <form className="reprint-search" onSubmit={(event) => void search(event)}>
          <label className="search-control"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search order ID, file, customer or phone…" aria-label="Search previous orders" /></label>
          <label className="field-label reprint-profile-select">Print setup<select className="input-control" value={profileId} onChange={(event) => setProfileId(event.target.value)}><option value="">Restore original settings</option>{profiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id}>Use {profile.profile_name}</option>)}</select></label>
          <button className="button button-primary" type="submit" disabled={loading}><Search size={15} /> Search orders</button>
        </form>
        {newReprint && <div className="reprint-success"><CheckCircle2 size={17} /><div><strong>New order {newReprint.order_id}</strong><span>{money(newReprint.total_price)} · Pickup PIN {newReprint.pickup_pin} · Payment pending</span></div><Link className="button button-secondary button-small" to={`/track/${encodeURIComponent(newReprint.order_id)}?token=${encodeURIComponent(newReprint.tracking_token)}`}>Track <ArrowRight size={14} /></Link></div>}
        {loading && results.length === 0 ? <LoadingRows /> : results.length ? (
          <div className="reprint-results">
            {results.map((order) => (
              <article className="reprint-result" key={order.order_id}>
                <span className="tiny-file"><FileText size={16} /></span>
                <div className="reprint-result-main"><strong>{order.order_id}</strong><span title={order.filename}>{order.filename}</span><small>{order.customer_name || "Customer"} · {order.total_pages} pages · {formatDate(order.created_at)}</small></div>
                <div className="reprint-result-price"><strong>{money(order.total_price)}</strong><StatusBadge status={order.order_status} /></div>
                <button className="button button-secondary button-small" disabled={activeOrder !== null} onClick={() => void reprint(order)}>{activeOrder === order.order_id ? <LoaderCircle className="spin" size={15} /> : <Copy size={15} />} Reprint</button>
              </article>
            ))}
          </div>
        ) : <div className="table-empty"><div className="empty-doc"><Search size={20} /></div><strong>No previous orders found</strong><p>Try a different order ID, filename or customer phone number.</p></div>}
      </Card>
      <div className="queue-note"><ShieldCheck size={16} /><span>SmartPrint will not claim the job is printed or paid. The reprint starts as a new pending order for owner confirmation.</span></div>
    </div>
  );
}

function PrintingProfilesSection({ ownerId, printers, notify }: {
  ownerId: number;
  printers: PrinterDevice[];
  notify: (notice: ToastState | null) => void;
}) {
  type ProfileValues = {
    profile_name: string;
    printer_id: number | null;
    paper_size: string;
    orientation: string;
    color_mode: string;
    duplex: boolean;
    quality: string;
    copies: number;
  };
  const defaultValues: ProfileValues = {
    profile_name: "", printer_id: null, paper_size: "A4", orientation: "Portrait",
    color_mode: "Auto", duplex: false, quality: "Standard", copies: 1,
  };
  const [profiles, setProfiles] = useState<PrintingProfile[]>([]);
  const [values, setValues] = useState<ProfileValues>(defaultValues);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const loadProfiles = async () => {
    setLoading(true);
    try {
      const result = await api<ApiResult<PrintingProfile[]>>(`/profiles/?owner_id=${ownerId}`);
      if (!result.data) throw new Error("The printing profiles response was incomplete.");
      setProfiles(result.data);
    } catch (error) {
      notify({ tone: "error", message: error instanceof Error ? error.message : "Could not load printing profiles." });
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { void loadProfiles(); }, [ownerId]);

  const saveProfile = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      const payload = { ...values, owner_id: ownerId };
      await api(editingId ? `/profiles/${editingId}` : "/profiles/", {
        method: editingId ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setValues(defaultValues);
      setEditingId(null);
      await loadProfiles();
      notify({ tone: "success", message: editingId ? "Printing profile updated." : "Printing profile saved." });
    } catch (error) {
      notify({ tone: "error", message: error instanceof Error ? error.message : "Could not save this profile." });
    } finally {
      setSaving(false);
    }
  };

  const editProfile = (profile: PrintingProfile) => {
    setEditingId(profile.profile_id);
    setValues({
      profile_name: profile.profile_name, printer_id: profile.printer_id, paper_size: profile.paper_size,
      orientation: profile.orientation, color_mode: profile.color_mode, duplex: profile.duplex,
      quality: profile.quality, copies: profile.copies,
    });
  };

  const removeProfile = async (profile: PrintingProfile) => {
    if (!window.confirm(`Delete the "${profile.profile_name}" profile?`)) return;
    try {
      await api(`/profiles/${profile.profile_id}?owner_id=${ownerId}`, { method: "DELETE" });
      setProfiles((current) => current.filter((item) => item.profile_id !== profile.profile_id));
      if (editingId === profile.profile_id) { setEditingId(null); setValues(defaultValues); }
      notify({ tone: "success", message: "Printing profile deleted." });
    } catch (error) {
      notify({ tone: "error", message: error instanceof Error ? error.message : "Could not delete the profile." });
    }
  };

  return (
    <div className="dashboard-section profile-layout">
      <Card className="profile-form-card">
        <SectionHeading eyebrow={editingId ? "EDIT PROFILE" : "REUSABLE PRINT SETUP"} title={editingId ? "Update profile" : "Create a printing profile"} detail="Save a setup once and reuse it for repeat jobs." />
        <form className="profile-form" onSubmit={(event) => void saveProfile(event)}>
          <label className="field-label">Profile name<input className="input-control" value={values.profile_name} onChange={(event) => setValues((current) => ({ ...current, profile_name: event.target.value }))} placeholder="e.g. College Notes" maxLength={100} required /></label>
          <label className="field-label">Printer<select className="input-control" value={values.printer_id ?? ""} onChange={(event) => setValues((current) => ({ ...current, printer_id: event.target.value ? Number(event.target.value) : null }))}><option value="">Automatic / not assigned</option>{printers.map((printer) => <option key={printer.printer_id} value={printer.printer_id}>{printer.printer_name}</option>)}</select></label>
          <label className="field-label">Paper size<select className="input-control" value={values.paper_size} onChange={(event) => setValues((current) => ({ ...current, paper_size: event.target.value }))}>{["A3", "A4", "A5", "Letter", "Legal", "Custom"].map((size) => <option key={size}>{size}</option>)}</select></label>
          <label className="field-label">Orientation<select className="input-control" value={values.orientation} onChange={(event) => setValues((current) => ({ ...current, orientation: event.target.value }))}><option>Portrait</option><option>Landscape</option></select></label>
          <label className="field-label">Color mode<select className="input-control" value={values.color_mode} onChange={(event) => setValues((current) => ({ ...current, color_mode: event.target.value }))}><option>Auto</option><option>Color</option><option>B&amp;W</option><option>Mixed</option></select></label>
          <label className="field-label">Quality<select className="input-control" value={values.quality} onChange={(event) => setValues((current) => ({ ...current, quality: event.target.value }))}><option>Draft</option><option>Standard</option><option>High</option></select></label>
          <label className="field-label">Copies<input className="input-control" type="number" min={1} max={99} value={values.copies} onChange={(event) => setValues((current) => ({ ...current, copies: Number(event.target.value) }))} required /></label>
          <label className="duplex-control"><input type="checkbox" checked={values.duplex} onChange={(event) => setValues((current) => ({ ...current, duplex: event.target.checked }))} /><span><strong>Two-sided printing</strong><small>Apply only when the selected printer supports duplex.</small></span></label>
          <div className="profile-form-actions"><button className="button button-primary" type="submit" disabled={saving}>{saving ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}{editingId ? "Update profile" : "Save profile"}</button>{editingId && <button className="button button-secondary" type="button" onClick={() => { setEditingId(null); setValues(defaultValues); }}>Cancel</button>}</div>
        </form>
      </Card>
      <Card className="profile-list-card">
        <SectionHeading eyebrow="SAVED PROFILES" title="Your print setups" detail={`${profiles.length} reusable profile${profiles.length === 1 ? "" : "s"}`} />
        {loading ? <LoadingRows /> : profiles.length ? <div className="saved-profile-list">{profiles.map((profile) => (
          <article className="saved-profile" key={profile.profile_id}>
            <span className="metric-icon icon-purple"><Printer size={17} /></span>
            <div className="saved-profile-info"><strong>{profile.profile_name}</strong><span>{[profile.paper_size, profile.orientation, profile.color_mode, profile.duplex ? "Duplex" : "Single-sided", profile.quality, `${profile.copies} ${profile.copies === 1 ? "copy" : "copies"}`].join(" · ")}</span><small>{printers.find((printer) => printer.printer_id === profile.printer_id)?.printer_name || "No printer assigned"}</small></div>
            <div className="saved-profile-actions"><button className="button button-secondary button-small" onClick={() => editProfile(profile)}>Edit</button><button className="icon-button" aria-label={`Delete ${profile.profile_name}`} onClick={() => void removeProfile(profile)}><X size={15} /></button></div>
          </article>
        ))}</div> : <div className="table-empty"><div className="empty-doc"><Save size={20} /></div><strong>No profiles saved yet</strong><p>Create profiles such as College Notes, Resume or Photo Printing.</p></div>}
      </Card>
      <div className="queue-note"><ShieldCheck size={16} /><span>Profiles save owner preferences only; selecting one never starts a printer job or changes settings without confirmation.</span></div>
    </div>
  );
}

function CustomersSection({ searchPhone, setSearchPhone, results, onSubmit, onOrder }: { searchPhone: string; setSearchPhone: (value: string) => void; results: Order[]; onSubmit: (event: FormEvent) => void; onOrder: (order: Order) => void }) {
  return (
    <div className="dashboard-section">
      <Card className="customer-search-card">
        <div className="customer-search-icon"><Users size={20} /></div><div className="customer-search-copy"><span className="eyebrow">CUSTOMER LOOKUP</span><h2>Find a customer’s orders</h2><p>Search by the phone number used when the print order was placed.</p></div>
        <form className="customer-search-form" onSubmit={onSubmit}><label className="search-control"><Search size={16} /><input type="tel" value={searchPhone} onChange={(event) => setSearchPhone(event.target.value)} placeholder="Enter phone number" aria-label="Customer phone number" required /></label><button className="button button-primary" type="submit">Search <ArrowRight size={16} /></button></form>
      </Card>
      {results.length > 0 && <Card className="orders-table-card"><SectionHeading eyebrow="SEARCH RESULTS" title={`${results.length} order${results.length === 1 ? "" : "s"} found`} /><OrdersTable orders={results} onOrder={onOrder} /></Card>}
      <div className="data-availability-note"><Users size={16} /><span>The existing backend provides customer lookup by phone through orders. It does not expose a customer directory or customer profile endpoint.</span></div>
    </div>
  );
}

function InventorySection({ inventory, onSave, updatingStock }: { inventory: InventoryItem[]; onSave: (item: InventoryItem, stock: number) => void; updatingStock: number | null }) {
  return (
    <div className="dashboard-section">
      <div className="inventory-grid">{inventory.map((item, index) => {
        const low = item.current_stock <= item.minimum_stock;
        const percentage = Math.min(100, Math.round(item.current_stock / Math.max(item.minimum_stock * 2, 1) * 100));
        return <motion.div key={item.inventory_id} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: index * .04 }}><InventoryCard item={item} low={low} percentage={percentage} onSave={onSave} updating={updatingStock === item.inventory_id} /></motion.div>;
      })}</div>
      {inventory.length === 0 && <Card className="table-empty"><div className="empty-doc"><Package size={21} /></div><strong>Inventory is empty</strong><p>Add inventory records in your shop database to track stock here.</p></Card>}
      <div className="queue-note"><Package size={16} /><span>Stock levels can be updated here. Creating new inventory items is not supported by the current backend API.</span></div>
    </div>
  );
}

function InventoryCard({ item, low, percentage, onSave, updating }: { item: InventoryItem; low: boolean; percentage: number; onSave: (item: InventoryItem, stock: number) => void; updating: boolean }) {
  const [stock, setStock] = useState(String(item.current_stock));
  useEffect(() => setStock(String(item.current_stock)), [item.current_stock]);
  return (
    <Card className={`inventory-card ${low ? "inventory-low" : ""}`}>
      <div className="inventory-card-top"><span className="inventory-icon"><Package size={18} /></span><span className={`inventory-label ${low ? "low-stock-label" : ""}`}>{low ? "Low stock" : "In stock"}</span></div>
      <h3>{item.item_name}</h3><p>Minimum level: {item.minimum_stock} {item.unit_measure}</p>
      <div className="stock-meter"><span style={{ width: `${percentage}%` }} /></div>
      <form className="stock-update-form" onSubmit={(event) => { event.preventDefault(); const amount = Number(stock); if (Number.isInteger(amount) && amount >= 0) onSave(item, amount); }}>
        <label>Current stock<input type="number" min="0" step="1" value={stock} onChange={(event) => setStock(event.target.value)} aria-label={`Current stock for ${item.item_name}`} /></label>
        <button className="button button-secondary button-small" type="submit" disabled={updating}>{updating ? <LoaderCircle className="spin" size={15} /> : "Save"}</button>
      </form>
    </Card>
  );
}

function PrintersSection({ printers, onAdd }: { printers: PrinterDevice[]; onAdd: () => void }) {
  return (
    <div className="dashboard-section">
      <Card className="printer-page-card">
        <SectionHeading eyebrow="CONNECTED DEVICES" title="Your printers" detail={`${printers.length} printer${printers.length === 1 ? "" : "s"} registered in the current workspace.`} action={<button className="button button-primary button-small" onClick={onAdd}><FilePlus2 size={15} /> Add printer</button>} />
        {printers.length > 0 ? <div className="printer-list">{printers.map((printer) => <div className="printer-row" key={printer.printer_id}><span className={`printer-device-icon ${printer.status.toLowerCase() === "online" ? "device-online" : ""}`}><Printer size={19} /></span><div className="printer-row-name"><strong>{printer.printer_name}</strong><small>{printer.model || "Model not specified"} · Branch {printer.branch_id}</small></div><StatusBadge status={printer.status} /><span className="printer-last-seen">Seen {formatDate(printer.last_seen)}</span></div>)}</div> : <div className="table-empty"><div className="empty-doc"><Printer size={21} /></div><strong>No printers registered</strong><p>Add a printer to start monitoring your print fleet.</p><button className="button button-primary" onClick={onAdd}>Add your first printer</button></div>}
      </Card>
      <div className="queue-note"><Activity size={16} /><span>Printer status reflects the data currently returned by the existing status endpoint.</span></div>
    </div>
  );
}

function ReportsSection({ rows }: { rows: Array<Record<string, string | number>> }) {
  const values = rows.map((row) => Number(row.monthly_revenue ?? row.daily_revenue ?? row.yearly_revenue ?? 0));
  const max = Math.max(...values, 1);
  return (
    <div className="dashboard-section">
      <Card className="report-card"><div className="report-header"><div><span className="eyebrow">MONTHLY REVENUE</span><h2>Completed print orders</h2><p>Revenue values come from the completed orders returned by the analytics API.</p></div><span className="metric-icon icon-teal"><TrendingUp size={18} /></span></div>
        {rows.length ? <div className="report-bars">{rows.slice().reverse().map((row, index) => {
          const label = row.month ? `${row.month}/${row.year}` : String(row.date ?? row.year);
          const value = Number(row.monthly_revenue ?? row.daily_revenue ?? row.yearly_revenue ?? 0);
          return <div className="report-bar-row" key={`${label}-${index}`}><span>{label}</span><div className="report-bar-track"><motion.span initial={{ width: 0 }} animate={{ width: `${Math.max(3, value / max * 100)}%` }} transition={{ duration: .55, delay: index * .04 }} /></div><strong>{money(value)}</strong></div>;
        })}</div> : <div className="table-empty"><div className="empty-doc"><TrendingUp size={21} /></div><strong>No completed revenue data yet</strong><p>Monthly report data will appear when completed orders are available.</p></div>}
      </Card>
      <div className="queue-note"><TrendingUp size={16} /><span>Orders trend and color/B&W analytics endpoints are also available from the dashboard backend.</span></div>
    </div>
  );
}

function SettingsSection({ shopName, ownerId, notify }: { shopName: string; ownerId: number; notify: (notice: ToastState | null) => void }) {
  const uploadUrl = `${window.location.origin}/?owner_id=${encodeURIComponent(ownerId)}&shop=${encodeURIComponent(shopName)}`;
  const signOut = async () => {
    try {
      await api("/logout/", { method: "POST" });
      localStorage.removeItem("smartprint_owner_id");
      localStorage.removeItem("smartprint_shop_name");
      window.location.assign("/admin.html");
    } catch (error) {
      notify({ tone: "error", message: error instanceof Error ? error.message : "Could not sign out." });
    }
  };
  const download = () => {
    const svg = document.querySelector<SVGSVGElement>(".shop-qr svg");
    if (!svg) return;
    const blob = new Blob([new XMLSerializer().serializeToString(svg)], { type: "image/svg+xml" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "smartprint-shop-qr.svg";
    anchor.click();
    URL.revokeObjectURL(url);
  };
  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(uploadUrl);
      notify({ tone: "success", message: "Shop upload link copied." });
    } catch (error) {
      notify({ tone: "error", message: error instanceof Error ? error.message : "Could not copy the shop link." });
    }
  };
  const share = async () => {
    try {
      if (navigator.share) await navigator.share({ title: `${shopName} print upload`, url: uploadUrl });
      else await copyLink();
    } catch (error) {
      if (!(error instanceof Error && error.name === "AbortError")) {
        notify({ tone: "error", message: error instanceof Error ? error.message : "Could not share the shop link." });
      }
    }
  };
  return (
    <div className="dashboard-section settings-grid">
      <Card className="shop-qr-card"><div className="shop-qr-glow"><div className="shop-qr"><QRCodeSVG value={uploadUrl} size={174} bgColor="#ffffff" fgColor="#101820" level="H" includeMargin /></div></div><div className="shop-qr-copy"><span className="eyebrow">YOUR SHOP QR</span><h2>Make ordering effortless.</h2><p>Customers can scan this code to open your upload page with your shop already selected.</p><div className="shop-url"><span>{uploadUrl}</span><button className="icon-button" aria-label="Copy shop upload URL" onClick={() => void copyLink()}><Share2 size={15} /></button></div><div className="shop-qr-actions"><button className="button button-primary" onClick={download}><ArrowDownToLine size={15} /> Download QR</button><button className="button button-secondary" onClick={() => window.print()}><Printer size={15} /> Print</button><button className="button button-secondary" onClick={() => void share()}><Share2 size={15} /> Share link</button></div></div></Card>
      <Card className="settings-info"><span className="eyebrow">WORKSPACE</span><h3>{shopName}</h3><div className="settings-detail"><span>Shop ID</span><strong>{ownerId}</strong></div><div className="settings-detail"><span>Order intake</span><StatusBadge status="Active" /></div><div className="settings-detail"><span>Backend</span><strong>SmartPrint API</strong></div><button className="button button-quiet signout-button" onClick={() => void signOut()}><LogOut size={15} /> Sign out of this shop</button></Card>
    </div>
  );
}

function OrderDrawer({ order, ownerId, onClose, onComplete, notify }: {
  order: Order;
  ownerId: number;
  onClose: () => void;
  onComplete: () => void;
  notify: (notice: ToastState | null) => void;
}) {
  const [history, setHistory] = useState<Array<{ action: string; status: string; timestamp: string }>>([]);
  const [documents, setDocuments] = useState<OrderDocument[]>([]);
  const [printSettings, setPrintSettings] = useState<OrderPrintSettings | null>(null);
  const [confirmingSettings, setConfirmingSettings] = useState(false);
  const [trackingLink, setTrackingLink] = useState("");
  useEffect(() => {
    void Promise.all([
      api<ApiResult<Array<{ action: string; status: string; timestamp: string }>>>(`/progress/${encodeURIComponent(order.order_id)}`),
      api<ApiResult<OrderDocument[]>>(`/orders/${encodeURIComponent(order.order_id)}/documents?owner_id=${ownerId}`),
      api<ApiResult<OrderPrintSettings | null>>(`/orders/${encodeURIComponent(order.order_id)}/print-settings?owner_id=${ownerId}`),
    ])
      .then(([historyResult, documentResult, settingResult]) => {
        if (!historyResult.data || !documentResult.data) throw new Error("The order detail response was incomplete.");
        setHistory(historyResult.data);
        setDocuments(documentResult.data);
        setPrintSettings(settingResult.data || null);
      })
      .catch((error) => notify({ tone: "error", message: error instanceof Error ? error.message : "Could not load order history." }));
  }, [order.order_id, ownerId, notify]);

  const confirmSettings = async () => {
    if (!window.confirm("Confirm that you reviewed these requested print preferences? This does not start a printer job.")) return;
    setConfirmingSettings(true);
    try {
      const result = await api<ApiResult<OrderPrintSettings>>(`/orders/${encodeURIComponent(order.order_id)}/confirm-settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ owner_id: ownerId }),
      });
      if (!result.data) throw new Error("The print settings response was incomplete.");
      setPrintSettings(result.data);
      notify({ tone: "success", message: "Print preferences confirmed. No printer job was started." });
    } catch (error) {
      notify({ tone: "error", message: error instanceof Error ? error.message : "Could not confirm print preferences." });
    } finally {
      setConfirmingSettings(false);
    }
  };

  const createTrackingLink = async () => {
    try {
      const result = await api<{ status: string; tracking_token: string }>(
        `/orders/${encodeURIComponent(order.order_id)}/tracking-link`,
        { method: "POST" },
      );
      const link = `${window.location.origin}/track/${encodeURIComponent(order.order_id)}?token=${encodeURIComponent(result.tracking_token)}`;
      setTrackingLink(link);
      await navigator.clipboard.writeText(link);
      notify({ tone: "success", message: "Secure tracking link copied. Share it directly with this customer." });
    } catch (error) {
      notify({ tone: "error", message: error instanceof Error ? error.message : "Could not create a customer tracking link." });
    }
  };

  return (
    <motion.div className="drawer-backdrop" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <motion.aside className="order-drawer" initial={{ x: "100%" }} animate={{ x: 0 }} exit={{ x: "100%" }} transition={{ type: "spring", damping: 27, stiffness: 260 }} role="dialog" aria-modal="true" aria-labelledby="drawer-title">
        <div className="drawer-head"><div><span className="eyebrow">ORDER DETAILS</span><h2 id="drawer-title">{order.order_id}</h2></div><button className="icon-button" aria-label="Close order details" onClick={onClose}><X size={18} /></button></div>
        <div className="drawer-status"><StatusBadge status={order.order_status} /><span>Created {formatDate(order.created_at)}</span></div>
        <div className="drawer-section"><span className="eyebrow">DOCUMENTS</span>{documents.length ? documents.map((document) => <div className="drawer-file" key={`${document.filename}-${document.url}`}><span className="file-icon"><FileText size={18} /></span><div><strong>{document.filename}</strong><small>{document.total_pages ?? order.total_pages} pages</small></div><a className="icon-button" href={document.url} target="_blank" rel="noreferrer" aria-label={`Preview ${document.filename}`}><ArrowUpRight size={15} /></a></div>) : <p className="muted-copy">Original document files are not available.</p>}</div>
        <div className="drawer-section"><span className="eyebrow">PRINT BREAKDOWN</span><div className="detail-row"><span>Total pages</span><strong>{order.total_pages}</strong></div><div className="detail-row"><span>Black & white</span><strong>{order.bw_pages}</strong></div><div className="detail-row"><span>Color</span><strong>{order.color_pages}</strong></div><div className="detail-row"><span>Payment</span><StatusBadge status={order.payment_status || "Pending"} /></div><div className="drawer-total"><span>Order total</span><strong>{money(order.total_price)}</strong></div></div>
        {printSettings && <div className="drawer-section"><span className="eyebrow">PRINT PREFERENCES</span><div className="detail-row"><span>Setup</span><StatusBadge status={printSettings.owner_confirmed ? "Owner confirmed" : "Needs owner review"} /></div><div className="detail-row"><span>Paper / orientation</span><strong>{printSettings.paper_size} · {printSettings.orientation}</strong></div><div className="detail-row"><span>Color / quality</span><strong>{printSettings.color_mode} · {printSettings.quality}</strong></div><div className="detail-row"><span>Duplex / copies</span><strong>{printSettings.duplex ? "Two-sided" : "One-sided"} · {printSettings.copies}</strong></div><div className="detail-row"><span>Printer</span><strong>{printSettings.printer_id ? `Printer #${printSettings.printer_id}` : "Automatic / not assigned"}</strong></div>{!printSettings.owner_confirmed && <button className="button button-secondary confirm-settings-button" disabled={confirmingSettings} onClick={() => void confirmSettings()}>{confirmingSettings ? <LoaderCircle className="spin" size={15} /> : <CheckCircle2 size={15} />} Confirm preferences</button>}<small className="settings-confirm-note">Confirmation does not send a job to a printer.</small></div>}
        <div className="drawer-section"><span className="eyebrow">CUSTOMER / TIMELINE</span><div className="detail-row"><span>Customer ID</span><strong>{order.customer_id ?? "Not provided"}</strong></div><button className="button button-secondary" onClick={() => void createTrackingLink()}><Share2 size={15} /> Create customer tracking link</button>{trackingLink && <a className="tracking-share-link" href={trackingLink} target="_blank" rel="noreferrer">{trackingLink}</a>}{history.length ? <div className="drawer-history">{history.map((item, index) => <div key={`${item.timestamp}-${index}`}><span className="timeline-dot"><Check size={11} /></span><div><strong>{item.action}</strong><small>{item.status} · {formatDate(item.timestamp)}</small></div></div>)}</div> : <p className="muted-copy">No progress events have been recorded.</p>}</div>
        <div className="drawer-actions"><button className="button button-danger" onClick={onComplete}><CheckCircle2 size={15} /> Mark completed</button></div>
        <p className="drawer-warning">Mark an order completed only after owner verification. Source documents are retained for reprints.</p>
      </motion.aside>
    </motion.div>
  );
}

function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return <motion.div className="modal-backdrop" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><motion.section className="modal-card" initial={{ opacity: 0, scale: .97, y: 8 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: .98 }} role="dialog" aria-modal="true" aria-label={title}><div className="modal-head"><h2>{title}</h2><button className="icon-button" aria-label="Close" onClick={onClose}><X size={18} /></button></div>{children}</motion.section></motion.div>;
}

function LoadingRows() {
  return <div className="loading-rows" aria-label="Loading orders"><span /><span /><span /><span /></div>;
}

export default App;
