import { TENANTS, fleetSize } from "../lib/fixtures";

export function SignIn({ onSignIn }: { onSignIn: (tenantId: string) => void }) {
  return (
    <section className="signin" aria-labelledby="signin-title">
      <div className="hero">
        <h1 className="hero__title" id="signin-title">
          Whose fleet are you
          <br />
          looking after?
        </h1>
        <p className="hero__sub">Demo sign-in. Each admin sees one company only.</p>
      </div>
      <div className="signin__list" role="group" aria-label="Demo companies">
        {TENANTS.map((tenant) => (
          <button key={tenant.id} type="button" className="company" onClick={() => onSignIn(tenant.id)}>
            <span className="company__name">{tenant.name}</span>
            <span className="company__meta">Admin · {fleetSize(tenant.id)} devices</span>
          </button>
        ))}
      </div>
    </section>
  );
}
