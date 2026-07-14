/*
 * Simple English / Hindi toggle for the CSP dashboard.
 *
 * Static UI labels carry a data-i18n="key" (or data-i18n-ph for placeholders).
 * This swaps them between English and Hindi and remembers the choice. Customer
 * DATA is never marked and never translated — only fixed UI labels are.
 *
 * The Hindi wording here is a first DRAFT for Eko to review/approve; extend the
 * DICT below as more screens are covered.
 */
(function () {
  const DICT = {
    // Login
    "login.kicker":   { en: "CSP message automation", hi: "CSP संदेश स्वचालन" },
    "login.title":    { en: "Operator Login",         hi: "ऑपरेटर लॉगिन" },
    "login.cspid":    { en: "CSP ID",                 hi: "CSP आईडी" },
    "login.password": { en: "Password",               hi: "पासवर्ड" },
    "login.submit":   { en: "Login",                  hi: "लॉगिन करें" },

    "onboard.kicker":        { en: "First-time setup",            hi: "पहली बार सेटअप" },
    "onboard.title":         { en: "Set up your dashboard",       hi: "अपना डैशबोर्ड सेट करें" },
    "onboard.subtitle":      { en: "Choose your own login and enter your branch details. You will use these to sign in.", hi: "अपना लॉगिन चुनें और अपनी शाखा की जानकारी भरें। इन्हीं से आप साइन इन करेंगे।" },
    "onboard.login_section": { en: "Your login",                  hi: "आपका लॉगिन" },
    "onboard.login_id":      { en: "Login ID (your CSP code)",    hi: "लॉगिन आईडी (आपका CSP कोड)" },
    "onboard.password":      { en: "Password",                    hi: "पासवर्ड" },
    "onboard.confirm":       { en: "Confirm password",            hi: "पासवर्ड की पुष्टि करें" },
    "onboard.branch_section":{ en: "Your branch details",         hi: "आपकी शाखा की जानकारी" },
    "onboard.csp_name":      { en: "CSP / branch name (shown on messages)", hi: "CSP / शाखा का नाम (संदेशों पर दिखेगा)" },
    "onboard.branch_code":   { en: "SBI branch code",             hi: "SBI शाखा कोड" },
    "onboard.address":       { en: "Branch address",              hi: "शाखा का पता" },
    "onboard.phone":         { en: "CSP phone",                   hi: "CSP फ़ोन" },
    "onboard.submit":        { en: "Save & continue to login",    hi: "सहेजें और लॉगिन पर जाएँ" },
    // Common nav / actions
    "nav.logout":     { en: "Logout",                 hi: "लॉगआउट" },
    "nav.campaigns":  { en: "Campaigns",              hi: "अभियान" },
    "nav.documents":  { en: "Back to Documents",      hi: "दस्तावेज़ों पर वापस" },
    "nav.back":       { en: "Back",                   hi: "वापस" },
    "nav.backcases":  { en: "Back to Cases",          hi: "मामलों पर वापस" },
    "nav.backdash":   { en: "Back to Dashboard",      hi: "डैशबोर्ड पर वापस" },
    "rev.cancel":     { en: "Cancel",                 hi: "रद्द करें" },
    "rev.confirm":    { en: "Confirm & Create Cases", hi: "पुष्टि करें और मामले बनाएँ" },
    // Dashboard tabs
    "tab.overview":   { en: "Overview",               hi: "सारांश" },
    "tab.cases":      { en: "Cases",                  hi: "मामले" },
    "tab.reports":    { en: "Reports",                hi: "रिपोर्ट" },
    "tab.settings":   { en: "Settings",               hi: "सेटिंग" },
    // Dispatch controls
    "disp.startmsg":  { en: "Start Messaging",        hi: "संदेश भेजना शुरू करें" },
    "disp.pause":     { en: "Pause",                  hi: "रोकें" },
    "disp.resume":    { en: "Resume",                 hi: "जारी रखें" },
    "disp.stop":      { en: "Stop",                   hi: "बंद करें" },
    // Documents / upload
    "doc.upload":     { en: "Upload Bank Document",   hi: "बैंक दस्तावेज़ अपलोड करें" },
    "doc.process":    { en: "Process & Upload",       hi: "प्रोसेस करें और अपलोड करें" },
    // Case detail labels
    "cd.custinfo":    { en: "Customer Information",   hi: "ग्राहक जानकारी" },
    "cd.account":     { en: "Account number",         hi: "खाता संख्या" },
    "cd.mobile":      { en: "Mobile",                 hi: "मोबाइल" },
    "cd.father":      { en: "Father's name",          hi: "पिता का नाम" },
    "cd.band":        { en: "Balance band",           hi: "बैलेंस बैंड" },
    "cd.taluka":      { en: "Taluka",                 hi: "तालुका" },
    "cd.village":     { en: "Village",                hi: "गाँव" },
    "cd.address":     { en: "Address",                hi: "पता" },
    "cd.batch":       { en: "Batch",                  hi: "बैच" },
    "cd.biztrack":    { en: "Business Tracking",      hi: "व्यवसाय ट्रैकिंग" },
    "cd.status":      { en: "Status",                 hi: "स्थिति" },
    "cd.escalated":   { en: "Escalated",              hi: "एस्केलेट किया गया" },
    "cd.msgsent":     { en: "Message sent",           hi: "संदेश भेजा गया" },
    "cd.visited":     { en: "Visited",                hi: "मिलने आया" },
    "cd.closed":      { en: "Closed",                 hi: "बंद" },
    "cd.message":     { en: "Message",                hi: "संदेश" },
    "cd.commhist":    { en: "Communication History",  hi: "संचार इतिहास" },
    "cd.approvetitle":{ en: "This case has not been queued yet.", hi: "यह मामला अभी कतार में नहीं है।" },
    "cd.approvebtn":  { en: "Approve for Sending",    hi: "भेजने के लिए स्वीकृत करें" },
  };

  function lang() { return localStorage.getItem("csp_lang") || "en"; }

  function apply() {
    const L = lang();
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      const k = el.getAttribute("data-i18n");
      if (DICT[k] && DICT[k][L]) el.textContent = DICT[k][L];
    });
    document.querySelectorAll("[data-i18n-ph]").forEach(function (el) {
      const k = el.getAttribute("data-i18n-ph");
      if (DICT[k] && DICT[k][L]) el.setAttribute("placeholder", DICT[k][L]);
    });
    const btn = document.getElementById("langToggle");
    if (btn) btn.textContent = (L === "en") ? "हिंदी" : "English";
  }

  window.toggleLang = function () {
    localStorage.setItem("csp_lang", lang() === "en" ? "hi" : "en");
    apply();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", apply);
  } else {
    apply();
  }
})();
