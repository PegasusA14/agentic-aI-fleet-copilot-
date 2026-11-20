import { Check, X } from "lucide-react";
import { useId, useState } from "react";
import type { ProposalCard } from "../lib/types";

type Decision = "approved" | "rejected" | null;

interface ProposalViewProps {
  proposal: ProposalCard;
  onOpenEvidence: (refs: string[]) => void;
}

/** Only a person decides. Approval is two deliberate steps, and the result is labelled as simulated. */
export function ProposalView({ proposal, onOpenEvidence }: ProposalViewProps) {
  const [confirming, setConfirming] = useState(false);
  const [decision, setDecision] = useState<Decision>(null);
  const effectId = useId();

  return (
    <section className="proposal" aria-label="Proposed action">
      <div className="proposal__target">
        <span>Open a remediation ticket for {proposal.target_label}</span>
        <span className="token mono">{proposal.target_device_id}</span>
      </div>
      <p style={{ margin: 0 }}>{proposal.rationale}</p>
      <p className="proposal__effect" id={effectId}>
        {proposal.expected_effect}
      </p>
      <dl className="facts" style={{ paddingTop: 0, borderTop: 0 }}>
        {Object.entries(proposal.scope)
          .filter(([, value]) => value)
          .map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        <div>
          <dt>Proposal</dt>
          <dd className="mono">{proposal.payload_hash.slice(0, 12)}</dd>
        </div>
      </dl>
      <div className="sheet__foot">
        <button type="button" className="link-btn" onClick={() => onOpenEvidence(proposal.evidence_refs)}>
          Evidence for this proposal ({proposal.evidence_refs.length})
        </button>
      </div>

      {decision ? (
        <p className="proposal__effect" role="status" style={{ fontWeight: 600 }}>
          {decision === "approved"
            ? "Approved. Simulated: no ticket was created anywhere."
            : "Rejected. Nothing was changed."}
        </p>
      ) : confirming ? (
        <div className="proposal__confirm" role="group" aria-label="Confirm approval">
          <p>Approve this exact proposal? It will be recorded as a simulated ticket.</p>
          <div className="proposal__actions">
            <button type="button" className="btn btn--primary" onClick={() => setDecision("approved")}>
              <Check size={16} aria-hidden="true" style={{ verticalAlign: "-3px", marginRight: 6 }} />
              Yes, approve
            </button>
            <button type="button" className="btn" onClick={() => setConfirming(false)}>
              Go back
            </button>
          </div>
        </div>
      ) : (
        <div className="proposal__actions">
          <button type="button" className="btn btn--primary" aria-describedby={effectId} onClick={() => setConfirming(true)}>
            Approve
          </button>
          <button type="button" className="btn" onClick={() => setDecision("rejected")}>
            <X size={16} aria-hidden="true" style={{ verticalAlign: "-3px", marginRight: 6 }} />
            Reject
          </button>
        </div>
      )}
    </section>
  );
}
