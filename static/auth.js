/* Passwords go directly from the browser to the configured managed provider. */
'use strict';
window.qaIdentity = {
  config: null,
  async provider(action, body) {
    const key = this.config?.web_api_key;
    if (!key) throw new Error('Managed sign-in is not configured.');
    const res = await fetch(`https://identitytoolkit.googleapis.com/v1/accounts:${action}?key=${encodeURIComponent(key)}`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body), credentials:'omit', referrerPolicy:'no-referrer'
    });
    if (!res.ok) {
      // Google answers with a code such as "WEAK_PASSWORD : ..."; show what it means, never the raw response.
      let code = '';
      try { code = String((await res.json())?.error?.message || '').split(/[ :]/)[0]; } catch (e) { /* not JSON */ }
      throw new Error(this.explain(code));
    }
    return res.json();
  },
  explain(code) {
    const messages = {
      INVALID_LOGIN_CREDENTIALS: 'Email or password is not right. No account yet? Press "Create verified account" first.',
      EMAIL_NOT_FOUND: 'No account with this email yet. Press "Create verified account" first.',
      INVALID_PASSWORD: 'Email or password is not right. Use "Reset password" if you have forgotten it.',
      EMAIL_EXISTS: 'An account with this email already exists. Sign in, or use "Reset password".',
      WEAK_PASSWORD: 'Choose a password with at least 6 characters.',
      INVALID_EMAIL: 'That email address does not look right.',
      MISSING_PASSWORD: 'Enter a password.',
      MISSING_EMAIL: 'Enter your email address.',
      USER_DISABLED: 'This account has been disabled. Ask the owner.',
      TOO_MANY_ATTEMPTS_TRY_LATER: 'Too many attempts. Wait a few minutes and try again.',
      OPERATION_NOT_ALLOWED: 'Email sign-in is not enabled for QA. Ask the owner.',
    };
    return messages[code] || 'Sign-in could not be completed. Check your email and password, or try again in a minute.';
  },
  async signIn(email,password) {
    const identity = await this.provider('signInWithPassword',{email,password,returnSecureToken:true});
    // Keep the short-lived ID token in memory only, then discard it after exchange.
    const res = await fetch('/api/session',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-QA-Request':'1'},body:JSON.stringify({id_token:identity.idToken})});
    if (!res.ok) {const data=await res.json();throw new Error(data.detail || 'Sign-in failed.');}
  },
  async register(email,password) {
    const identity=await this.provider('signUp',{email,password,returnSecureToken:true});
    await this.provider('sendOobCode',{requestType:'VERIFY_EMAIL',idToken:identity.idToken});
    return 'Account created. Open the verification email we just sent (check spam), click the link, then come back and press "Enter workspace".';
  },
  async reset(email) {
    await this.provider('sendOobCode',{requestType:'PASSWORD_RESET',email});
    return 'If the account is eligible, a reset email will arrive.';
  }
};
