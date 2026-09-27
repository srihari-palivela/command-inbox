/**
 * Simulated telephony. In production a CPaaS adapter (Twilio, Exotel) streams real transcript events
 * and a recording URL; the call record and ticket updates are the same.
 */
export type Turn = { who: 'You' | 'Customer'; text: string };

export const CALL_SCRIPTS: Record<number, Turn[]> = {
  48199: [
    {
      who: 'You',
      text: 'Mrs Sheikh, this is Priyanka from the bank, calling about your dispute — DSP-11207.',
    },
    { who: 'Customer', text: 'Finally. I have written three times. I was about to go to the ombudsman.' },
    {
      who: 'You',
      text: 'You were right to chase us. I am raising the provisional credit of ₹94,500 today — it should show by tomorrow morning.',
    },
    { who: 'Customer', text: 'Today? Alright. And the investigation itself?' },
    {
      who: 'You',
      text: 'The chargeback goes to the card network this week. I will email you a dated commitment within the hour, on this call’s reference.',
    },
    { who: 'Customer', text: 'Thank you. If the credit shows tomorrow, I will hold off on the ombudsman.' },
  ],
  48211: [
    {
      who: 'You',
      text: 'Ms Raghavan, calling to verify the stop payment before we execute — cheque four four nine one two zero?',
    },
    { who: 'Customer', text: 'Correct, 449120, eighteen lakh forty thousand, to Velan Logistics.' },
    {
      who: 'You',
      text: 'Confirmed. It goes for dual approval now; you will get written confirmation before end of day.',
    },
    { who: 'Customer', text: 'Good. Please copy our CFO on the confirmation.' },
  ],
};

export const CALL_OUTCOMES: Record<number, { summary: string; updates: string[]; completeSubtask?: string }> =
  {
    48199: {
      summary:
        'Customer agreed to hold the ombudsman filing if the provisional credit of ₹94,500 lands by tomorrow morning. You committed to a dated written commitment within the hour and a chargeback filing this week.',
      updates: [
        'Priority stays P1 · commitment deadline added: today +1h',
        'Sub-task “Give the customer a dated commitment” marked done',
        'Sentiment updated: de-escalating — hold-off agreed',
      ],
      completeSubtask: 'c3',
    },
    48211: {
      summary:
        'Signatory verbally confirmed cheque 449120 for ₹18,40,000 to Velan Logistics. Requested CFO be copied on the written confirmation.',
      updates: [
        'Verbal verification recorded against the mandate check',
        'CC added to confirmation: CFO, Sundaram Textiles',
        'Ready for checker approval',
      ],
    },
  };

export function scriptFor(number: number, subject: string): Turn[] {
  return (
    CALL_SCRIPTS[number] ?? [
      { who: 'You', text: `Calling about your request — ${subject.slice(0, 60)}.` },
      { who: 'Customer', text: 'Yes, thanks for calling back.' },
      { who: 'You', text: 'I have everything on screen; let me confirm the details with you now.' },
      { who: 'Customer', text: 'That all matches. When will it be done?' },
      { who: 'You', text: 'You will have written confirmation today.' },
    ]
  );
}

export function outcomeFor(number: number) {
  return (
    CALL_OUTCOMES[number] ?? {
      summary:
        'Spoke with the customer, confirmed the details on file and agreed the next step on the ticket.',
      updates: ['Call outcome noted on the ticket', 'No change to priority or routing'],
    }
  );
}

export const DIAL_MS = 1400;
export const LINE_MS = 2600;
