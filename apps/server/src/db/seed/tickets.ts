/**
 * The designed ticket scenario. Clock times in the prototype are relative to 12:02 "now"; here each
 * time is expressed as minutes before now so the scenario is always current.
 */
import type { BoardKey, DeptName, PersonName } from './data.js';

export interface SeedMessage {
  who: string;
  minAgo: number;
  body: string;
  internal?: boolean;
}

export interface SeedAction {
  code: string;
  validation: string;
  fields: { label: string; value: string; source: string }[];
  /** 'drafted' = waiting at the gate; 'executed' = the AI already did it (auto cell). */
  state: 'drafted' | 'executed';
  account: string;
}

export interface SeedDraft {
  subject: string;
  body: string;
  flagged: string[];
  cites: string[]; // KDOCS keys
}

export interface SeedBrief {
  why: string;
  summary: string;
  context: [string, string][];
  suggestions: [string, string][];
}

export interface SeedTicket {
  number: number;
  board: BoardKey;
  lane: 'auto' | 'draft' | 'manual';
  laneNote: string;
  status: 'triaging' | 'awaiting_approval' | 'executing' | 'with_human' | 'waiting_customer' | 'resolved';
  priority: 'P1' | 'P2' | 'P3' | 'P4';
  segment: string;
  subject: string;
  customer: { name: string; email: string; cif: string; account: string; segment: string; since: number; phone: string };
  queryType: string;
  bucketLabel?: string;
  dept: DeptName | null;
  deptLabel?: string;
  confidence: number;
  owner: PersonName | 'AI' | 'Unassigned';
  receivedMinAgo: number;
  slaMinutes: number;
  dueInMin: number | null;
  nextMove: string;
  latencyMs: number;
  category: string;
  subcategory: string;
  product: string;
  regulatoryFlag?: string;
  reopenCount?: number;
  sentiment?: string;
  thread?: SeedMessage[];
  evidence?: [string, string, string][];
  reasoning?: string;
  action?: SeedAction;
  draft?: SeedDraft;
  brief?: SeedBrief;
  splitProposed?: boolean;
  gap?: number;
  dispute?: string;
  log?: { kind: 'note' | 'public'; who: PersonName; minAgo: number; text: string }[];
  attachments?: [string, string, string][];
  loggedMinutes?: number;
  resolvedMinAgo?: number;
}

const CUST = {
  meera: { name: 'Meera Raghavan', email: 'm.raghavan@sundaramtextiles.in', cif: 'CIF 8830412', account: 'A/C ••4471', segment: 'Corporate', since: 2019, phone: '+91 98400 14471' },
  anand: { name: 'Anand Krishnan', email: 'anand.k1974@gmail.com', cif: 'CIF 4410277', account: 'A/C ••2098', segment: 'NRI', since: 2019, phone: '+971 50 441 2098' },
  fatima: { name: 'Fatima Sheikh', email: 'fatima.sheikh@outlook.com', cif: 'CIF 2290844', account: 'A/C ••7712', segment: 'Retail', since: 2015, phone: '+91 98205 07712' },
  rohit: { name: 'Rohit Desai', email: 'rohit.desai@zohomail.in', cif: 'CIF 7712009', account: 'A/C ••3390', segment: 'Retail', since: 2019, phone: '+91 99300 03390' },
  vikram: { name: 'Vikram Bose', email: 'vikram@boseexports.co.in', cif: 'CIF 6620188', account: 'A/C ••5540', segment: 'SME', since: 2019, phone: '+91 98310 05540' },
  priya: { name: 'Priya Nambiar', email: 'priya.nambiar@gmail.com', cif: 'CIF 3390771', account: 'A/C ••8823', segment: 'Retail', since: 2015, phone: '+91 94470 08823' },
} as const;

const c = (name: string, email: string, cif: string, account: string, segment = 'Retail', since = 2018) => ({
  name,
  email,
  cif,
  account,
  segment,
  since,
  phone: '+91 90000 ' + account.slice(-4).padStart(5, '0'),
});

// ── The six tickets in P. Sharma's inbox ─────────────────────────────────────
export const DETAILED: SeedTicket[] = [
  {
    number: 48211, board: 'trade', lane: 'auto', laneNote: 'Filled in, waiting on two approvers', status: 'awaiting_approval', priority: 'P1', segment: 'Corporate',
    subject: 'Urgent — stop payment on cheque 449120, ₹18,40,000, drawn on our CC account',
    customer: CUST.meera, queryType: 'Stop payment instruction', dept: 'Trade & Payments', confidence: 0.94, owner: 'P. Sharma',
    receivedMinAgo: 168, slaMinutes: 240, dueInMin: 72, nextMove: 'Your approval + checker', latencyMs: 1900,
    category: 'Transactions', subcategory: 'Instrument instructions', product: 'Current account', loggedMinutes: 12,
    thread: [
      { who: 'Meera Raghavan', minAgo: 168, body: 'Please place an immediate stop on cheque number 449120 for ₹18,40,000 issued to Velan Logistics on 12 July. The consignment was never delivered and we have terminated the contract. The cheque is drawn on our cash credit account ending 4471. Confirm the stop is in force before end of day — the beneficiary has indicated they will bank it tomorrow morning.' },
      { who: 'Meera Raghavan', minAgo: 151, body: 'Adding our CFO on copy. Please treat as urgent.' },
    ],
    evidence: [
      ['INTENT', '"place an immediate stop on cheque number 449120"', 'exact action verb'],
      ['ENTITY', 'cheque no. 449120 · ₹18,40,000 · A/C ••4471', 'all mandatory fields present'],
      ['AUTH', 'sender is a registered authorised signatory on CIF 8830412', 'mandate check passed'],
    ],
    reasoning: 'Single unambiguous intent, matched to the Stop Payment action template with all five mandatory fields extractable from the thread. Sender verified as an authorised signatory against the mandate register, and the instrument is unpaid as of 09:41. Cheque number, amount and account all agree with the core banking record, so no clarification round-trip is needed. This one cannot be undone and money moves, so it stops here for your approval rather than acting on its own.',
    action: {
      code: 'ACT-STP-014', state: 'drafted', account: '••••4471',
      validation: 'Instrument unpaid as of 09:41. Sufficient balance held. No conflicting instruction on this cheque.',
      fields: [
        { label: 'Account number', value: '••••4471', source: 'mandate register' },
        { label: 'Cheque number', value: '449120', source: 'email body' },
        { label: 'Amount', value: '₹18,40,000.00', source: 'email body' },
        { label: 'Instrument date', value: '12-Jul-2026', source: 'email body' },
        { label: 'Reason code', value: 'CONTRACT_TERMINATED', source: 'inferred · 0.91' },
        { label: 'Requested by', value: 'M. Raghavan (Signatory A)', source: 'CIF 8830412' },
      ],
    },
    log: [{ kind: 'note', who: 'P. Sharma', minAgo: 138, text: 'Called the customer to confirm the cheque number verbally before approving. She confirmed 449120.' }],
    attachments: [['PDF', 'Board resolution — signatories.pdf', '412 KB'], ['JPG', 'Cheque 449120 scan.jpg', '1.2 MB']],
  },
  {
    number: 48207, board: 'nri', lane: 'draft', laneNote: 'Cited draft ready to send', status: 'awaiting_approval', priority: 'P3', segment: 'NRI',
    subject: 'What documents do I need to add my daughter as a joint holder on my NRE account?',
    customer: CUST.anand, queryType: 'Account maintenance', bucketLabel: 'Account maintenance — joint holder addition', dept: 'Retail Service Desk', confidence: 0.88, owner: 'P. Sharma',
    receivedMinAgo: 190, slaMinutes: 1440, dueInMin: 1180, nextMove: 'Review & send draft', latencyMs: 1400,
    category: 'Servicing', subcategory: 'Information request', product: 'Savings / NRE', loggedMinutes: 7,
    thread: [
      { who: 'Anand Krishnan', minAgo: 190, body: 'I hold an NRE savings account with you and I am currently in Dubai. I would like to add my daughter, who is resident in Chennai, as a joint holder. What is the document list and can it be done without me travelling to India? Also please confirm whether the account status changes if a resident is added.' },
    ],
    evidence: [
      ['INTENT', 'informational — document list + process for joint holder addition', 'no action requested yet'],
      ['MATCH', 'NRE Account Operations Manual §4.2 covers resident joint holders', 'approved source'],
      ['FLAG', 'former-or-survivor mode is mandatory for resident joint holders', 'must be stated'],
    ],
    reasoning: 'Purely informational request with a clean match into approved NRE operations content. Two sources cover the full answer: the document checklist and the mandatory operating mode for a resident joint holder. Nothing here changes the account, so no action template is attached. Draft is grounded only in knowledge-manager-approved material — no model recall is used for the policy statements.',
    draft: {
      subject: 'Re: Adding a joint holder to your NRE savings account',
      cites: ['nre', 'fema'],
      flagged: ['“former or survivor”'],
      body: [
        'Dear Mr Krishnan,',
        'Thank you for writing in. You can add your daughter as a joint holder on your NRE savings account without travelling to India. We will need the following, all of which may be submitted through your nearest Indian mission or a notary in the UAE [1]:',
        '• A signed joint holder addition request from both parties  • Your daughter’s PAN, Aadhaar and a recent photograph  • Address proof for your daughter  • A copy of your passport and valid visa page, attested',
        'One important point: where a resident individual is added to an NRE account, the account must be operated on a “former or survivor” basis, with the non-resident holder as the former. The account retains its NRE status and its tax treatment is unchanged [2].',
        'Once we receive the documents the update is completed within two working days. We will confirm by email.',
        'Warm regards,',
      ].join('\n\n'),
    },
  },
  {
    number: 48199, board: 'disputes', lane: 'manual', laneNote: 'Held back — upset customer, regulator named', status: 'with_human', priority: 'P1', segment: 'Retail',
    subject: 'Third time writing about the unauthorised debit of ₹94,500 — will escalate to the ombudsman',
    customer: CUST.fatima, queryType: 'Disputed transactions', bucketLabel: 'Disputed transaction — unauthorised debit', dept: 'Chargeback & Disputes', confidence: 0.41, owner: 'P. Sharma',
    receivedMinAgo: 1102, slaMinutes: 480, dueInMin: 26, nextMove: 'Human commitment due', latencyMs: 2200,
    category: 'Complaints', subcategory: 'Disputed debit', product: 'Debit & credit cards', regulatoryFlag: 'Ombudsman named', reopenCount: 2, sentiment: 'escalating', loggedMinutes: 46,
    thread: [
      { who: 'Fatima Sheikh', minAgo: 1102, body: 'This is my third email. On 9 July there was a debit of ₹94,500 to a merchant I have never transacted with. I raised a dispute on 10 July (reference DSP-11207) and I have had nothing but an acknowledgement. If I do not hear from a human being with a resolution date by tomorrow I will be filing with the Banking Ombudsman and with my consumer forum.' },
      { who: 'Command Inbox · acknowledgement', minAgo: 1101, body: 'Acknowledged and routed to Chargeback & Disputes. No customer-facing content generated.', internal: true },
    ],
    evidence: [
      ['INTENT', 'multi-intent: dispute status + complaint + regulatory threat', 'cannot be handled as one'],
      ['SENTIMENT', 'severe dissatisfaction, third contact, ombudsman named', 'hard stop rule'],
      ['POLICY', 'regulatory-escalation keywords force human handling', 'policy 7.1'],
    ],
    reasoning: 'Confidence is below the bar for handling this automatically, and two hard stop rules fire: an explicit regulatory escalation and a repeat-contact sentiment signal. The agent will not draft customer-facing content here. Instead it has assembled the dispute history, the merchant record and the chargeback clock so you open this already briefed. Ageing is 26 minutes from breach, so it has been pushed to the top of your queue and your team lead has been notified.',
    brief: {
      why: 'Regulatory escalation · policy 7.1',
      summary: 'Fourth touch on a ₹94,500 disputed card debit first raised 10 July. Dispute DSP-11207 is open but has not moved past acknowledgement in 15 days — beyond the 5-day provisional credit window. Customer has named the Banking Ombudsman. Requires a human commitment with a dated resolution, and provisional credit is very likely due.',
      context: [
        ['Dispute ref', 'DSP-11207 · open 15 days'],
        ['Merchant', 'GLOBALPAY*ND8823, Singapore'],
        ['Time left to claim', '5 days to raise with the card network'],
        ['Provisional credit', 'Due — window lapsed 17 Jul'],
        ['Prior contacts', '3 emails, 1 branch visit'],
      ],
      suggestions: [
        ['Raise provisional credit ₹94,500', 'ACT-DSP-003'],
        ['Open chargeback with network', '5d left'],
        ['Call-back within 2h + dated commitment', 'recommended'],
        ['Flag as ombudsman-risk to Compliance', 'policy 7.1'],
      ],
    },
    dispute: 'DSP-11207 · open 15 days',
    log: [
      { kind: 'public', who: 'A. Fernandes', minAgo: 4302, text: 'Acknowledged receipt and apologised for the delay. Did not commit to a date pending the credit decision.' },
      { kind: 'note', who: 'S. Qureshi', minAgo: 237, text: 'Provisional credit is overdue. Raising it today regardless of the chargeback outcome — we are outside the window.' },
    ],
    attachments: [['PNG', 'Statement showing the debit.png', '684 KB']],
  },
  {
    number: 48195, board: 'retail', lane: 'auto', laneNote: 'Can be undone — the AI already did it', status: 'resolved', priority: 'P4', segment: 'Retail',
    subject: 'Please re-issue the account statement for April to June, the PDF is password protected',
    customer: CUST.rohit, queryType: 'Statement re-issue', bucketLabel: 'Statement re-issue request', dept: 'Retail Service Desk', confidence: 0.97, owner: 'P. Sharma',
    receivedMinAgo: 264, slaMinutes: 1440, dueInMin: 1176, nextMove: 'Auto-executed 07:38', latencyMs: 900, resolvedMinAgo: 264,
    category: 'Servicing', subcategory: 'Documents', product: 'Savings account', loggedMinutes: 0,
    thread: [
      { who: 'Rohit Desai', minAgo: 264, body: 'Could you send me the statement for my savings account for April to June this year. The one I downloaded from net banking is password protected and I need an unlocked copy for my CA. Same email is fine.' },
    ],
    evidence: [
      ['INTENT', 'statement re-issue, explicit period given', 'single intent'],
      ['ENTITY', 'Apr–Jun 2026 · A/C ••3390 · registered email', 'all fields resolved'],
      ['RISK', 'can be undone, no money moves, own account only', 'the one group the AI may act in'],
    ],
    reasoning: 'The highest-confidence pattern we handle. The action can be undone, no money moves, delivery is restricted to the registered email on file, and this is the one risk group where the AI is allowed to act alone. The agent executed at 07:38 and logged it; you are seeing the record, not a request.',
    action: {
      code: 'ACT-STM-002', state: 'executed', account: '••••3390',
      validation: 'Delivered to registered email only. Password-free copy per request. Logged to audit trail.',
      fields: [
        { label: 'Account number', value: '••••3390', source: 'CIF 7712009' },
        { label: 'Period from', value: '01-Apr-2026', source: 'email body' },
        { label: 'Period to', value: '30-Jun-2026', source: 'email body' },
        { label: 'Format', value: 'PDF, unsecured', source: 'email body' },
        { label: 'Delivery', value: 'Registered email', source: 'policy default' },
        { label: 'Charge', value: 'Waived · first request', source: 'fee schedule' },
      ],
    },
  },
  {
    number: 48188, board: 'trade', lane: 'draft', laneNote: 'Not confident enough — read it before sending', status: 'awaiting_approval', priority: 'P2', segment: 'SME',
    subject: 'Can we use our export receivables to margin a forward contract, and what is the tenor cap?',
    customer: CUST.vikram, queryType: 'Trade finance advisory', bucketLabel: 'Trade finance — forward cover eligibility', dept: 'Trade & Payments', confidence: 0.72, owner: 'P. Sharma',
    receivedMinAgo: 342, slaMinutes: 1440, dueInMin: 862, nextMove: 'Check flagged paragraph', latencyMs: 2600,
    category: 'Servicing', subcategory: 'Advisory', product: 'Trade finance', loggedMinutes: 7,
    thread: [
      { who: 'Vikram Bose', minAgo: 1027, body: 'We have export receivables of about USD 640k landing over the next five months. Can those receivables be used as margin for a forward contract instead of a cash margin, and what is the maximum tenor you will book against a firm order?' },
      { who: 'Vikram Bose', minAgo: 342, body: 'Also — does it change if part of the order is on DA 90 days terms?' },
    ],
    evidence: [
      ['INTENT', 'two informational questions, second added overnight', 'compound query'],
      ['GAP', 'no approved content covers receivables-as-margin for DA terms', 'gap ticket raised'],
      ['MATCH', 'Forward Cover Policy §2.1 covers tenor cap only', 'partial coverage'],
    ],
    reasoning: 'Confidence sits below the 0.78 bar because only half the question has approved coverage. The tenor cap is answerable from the Forward Cover Policy; receivables-as-margin under DA 90 terms has no approved source, so the agent has drafted the answerable part, marked the gap explicitly rather than filling it, and raised a knowledge gap ticket to the Trade Finance owner. Do not send this without checking the flagged paragraph.',
    draft: {
      subject: 'Re: Forward cover against export receivables',
      cites: ['fwd'],
      flagged: [
        'On using receivables in place of a cash margin — and specifically where part of the order is on DA 90 day terms — I do not yet have an approved position I can commit to in writing. This has been raised with Trade Finance as gap GAP-0412 and I will revert with a definitive answer.',
      ],
      body: [
        'Dear Mr Bose,',
        'On tenor: for a firm export order we will book forward cover up to the earlier of the expected realisation date or 12 months from the contract date, with rollover permitted once against documentary evidence of a shipment delay [1].',
        'On using receivables in place of a cash margin — and specifically where part of the order is on DA 90 day terms — I do not yet have an approved position I can commit to in writing. This has been raised with Trade Finance as gap GAP-0412 and I will revert with a definitive answer.',
        'If the timing is pressing, our trade desk can hold an indicative rate for 24 hours while this is confirmed.',
        'Warm regards,',
      ].join('\n\n'),
    },
    gap: 412,
    attachments: [['XLSX', 'Receivables schedule Q3.xlsx', '96 KB']],
  },
  {
    number: 48174, board: 'retail', lane: 'manual', laneNote: 'Two separate questions in one email', status: 'with_human', priority: 'P2', segment: 'Retail',
    subject: 'Loan foreclosure amount and also why has my credit card limit dropped?',
    customer: CUST.priya, queryType: 'Multi-intent — lending + cards', dept: 'Retail Lending', confidence: 0.55, owner: 'P. Sharma',
    receivedMinAgo: 410, slaMinutes: 1440, dueInMin: 588, nextMove: 'Split into two tickets', latencyMs: 1700,
    category: 'Servicing', subcategory: 'Multi-intent', product: 'Personal loan + credit card', splitProposed: true, loggedMinutes: 46,
    thread: [
      { who: 'Priya Nambiar', minAgo: 410, body: 'Two things. I want the foreclosure amount for my personal loan as of 31 July including any charges. Separately, my credit card limit was reduced from ₹4,00,000 to ₹1,50,000 without any notice and I would like an explanation.' },
    ],
    evidence: [
      ['INTENT', 'two intents spanning two departments', 'no single owner'],
      ['POLICY', 'limit reduction reasons need a credit-decision human', 'policy 5.4'],
      ['SPLIT', 'agent proposes splitting into two child tickets', 'awaiting your call'],
    ],
    reasoning: 'Two distinct intents owned by two different departments, one of which — explaining a credit limit reduction — requires a human credit decision under policy 5.4. Rather than guess a primary intent, the agent has proposed a split into two child tickets, pre-filled the foreclosure figure from the loan system, and held the cards question for a credit officer.',
    brief: {
      why: 'Multi-intent · policy 5.4',
      summary: 'Foreclosure quote is fully computable and pre-filled below. The credit limit reduction was a risk-model action on 2 July following a bureau score change — the reason must be communicated by a credit officer, not the agent. Recommended: split, answer the loan half today, route the cards half to Credit Risk.',
      context: [
        ['Loan account', 'PL ••4408 · outstanding ₹6,12,340'],
        ['Foreclosure 31 Jul', '₹6,29,187 incl. 2% charge'],
        ['Limit change', '2 Jul · risk model · bureau −68'],
        ['Notice sent', 'SMS only — no email trace'],
        ['Relationship', '11 years · no delinquency'],
      ],
      suggestions: [
        ['Split into two child tickets', 'recommended'],
        ['Send foreclosure quote', 'ACT-LON-007'],
        ['Route cards query to Credit Risk', 'policy 5.4'],
        ['Log missing email notice as a control gap', 'audit'],
      ],
    },
  },
];

// ── The rest of the board (lighter detail) ───────────────────────────────────
type Row = [
  number, // number
  'P1' | 'P2' | 'P3' | 'P4',
  string, // subject
  'auto' | 'draft' | 'manual',
  string, // query type
  DeptName | null,
  SeedTicket['status'],
  PersonName | 'AI' | 'Unassigned',
  number | null, // due in minutes (null = closed)
  number, // confidence
  string, // next move
  BoardKey,
  ReturnType<typeof c>,
  number, // received min ago
];

const ROWS: Row[] = [
  [48216, 'P4', 'Interest certificate for FY 2025-26', 'auto', 'Certificate requests', 'Retail Service Desk', 'triaging', 'AI', 1431, 0.96, 'Extracting fields', 'retail', c('Kavya Menon', 'kavya.menon@gmail.com', 'CIF 5511023', 'A/C ••6621'), 9],
  [48215, 'P3', 'Why was ₹590 debited as annual card fee?', 'draft', 'Balance & charge queries', 'Cards', 'triaging', 'AI', 1424, 0.91, 'Drafting cited reply', 'retail', c('Suresh Babu', 'suresh.babu@yahoo.co.in', 'CIF 6620411', 'CARD ••2214'), 16],
  [48214, 'P2', 'SWIFT trace for USD 42,000 remittance of 21 Jul', 'draft', 'SWIFT trace requests', 'Trade & Payments', 'triaging', 'AI', 668, 0.83, 'Awaiting core lookup', 'trade', c('Nitin Agarwal', 'nitin@agarwalimpex.com', 'CIF 7300912', 'A/C ••9012', 'SME'), 772],
  [48213, 'P4', 'Cheque book request, 50 leaves, same branch', 'auto', 'Statement re-issue', 'Retail Service Desk', 'executing', 'AI', 1339, 0.95, 'Writing to Finacle', 'retail', c('Lakshmi Iyer', 'lakshmi.iyer@gmail.com', 'CIF 4420871', 'A/C ••0871'), 101],
  [48212, 'P1', 'Reverse duplicate NEFT of ₹1,20,000 sent twice', 'auto', 'Disputed transactions', 'Trade & Payments', 'executing', 'R. Menon', 220, 0.87, 'Checker approving', 'trade', c('Harish Rao', 'harish.rao@raoandsons.in', 'CIF 8123004', 'A/C ••3004', 'SME'), 260],
  [48209, 'P3', 'Update registered mobile number, OTP done', 'auto', 'Account maintenance', 'Retail Service Desk', 'executing', 'AI', 1202, 0.89, 'Writing to Finacle', 'retail', c('Gopal Pillai', 'gopal.pillai@outlook.com', 'CIF 3301265', 'A/C ••1265'), 238],
  [48206, 'P3', 'Chargeback status for DSP-11009, card ••2214', 'draft', 'Chargeback status', 'Chargeback & Disputes', 'waiting_customer', 'L. Thomas', 1872, 0.64, 'Awaiting merchant invoice', 'disputes', c('Ritu Singh', 'ritu.singh@gmail.com', 'CIF 2290117', 'CARD ••2214'), 1400],
  [48204, 'P3', 'EMI reschedule after job change, 6 month gap', 'manual', 'EMI reschedule requests', 'Retail Lending', 'waiting_customer', 'L. Thomas', 2670, 0.48, 'Awaiting salary proof', 'retail', c('Manoj Kumar', 'manoj.kumar84@gmail.com', 'CIF 1180342', 'PL ••0342'), 1500],
  [48201, 'P2', 'Locker rent waiver for the second year', 'manual', 'Locker rent waiver', null, 'with_human', 'Unassigned', 415, 0.33, 'No team owns this query type', 'retail', c('Sheela Rao', 'sheela.rao@gmail.com', 'CIF 1100443', 'LKR ••0443'), 1025],
  [48198, 'P2', 'Nomination change for deceased joint holder', 'manual', 'Account maintenance', 'Retail Service Desk', 'with_human', 'A. Fernandes', 310, 0.39, 'Documents under review', 'retail', c('Joseph Mathew', 'joseph.mathew@gmail.com', 'CIF 9001772', 'A/C ••1772'), 1130],
  [48196, 'P3', 'Fee schedule for NRO account maintenance', 'draft', 'Balance & charge queries', 'Retail Service Desk', 'awaiting_approval', 'A. Fernandes', 984, 0.69, 'Source is 94 days stale', 'nri', c('Farhan Ali', 'farhan.ali@gmail.com', 'CIF 4410981', 'A/C ••0981', 'NRI'), 456],
  [48192, 'P4', 'Card temporarily blocked while travelling', 'auto', 'Disputed transactions', 'Cards', 'resolved', 'AI', null, 0.93, 'Hold placed 06:12', 'retail', c('Anita Desai', 'anita.desai@gmail.com', 'CIF 5522018', 'CARD ••2018'), 350],
  [48190, 'P4', 'Balance confirmation letter for visa application', 'auto', 'Certificate requests', 'Retail Service Desk', 'resolved', 'AI', null, 0.95, 'Emailed 05:48', 'retail', c('Rahul Verma', 'rahul.verma@gmail.com', 'CIF 6614420', 'A/C ••4420'), 374],
  [48186, 'P4', 'Late payment fee waiver, first miss in 4 years', 'auto', 'Balance & charge queries', 'Cards', 'resolved', 'A. Fernandes', null, 0.86, 'Waived, you approved', 'retail', c('Deepa Nair', 'deepa.nair@gmail.com', 'CIF 7733120', 'CARD ••3120'), 420],
  [48181, 'P4', 'Foreclosure quote for PL ••2201 as of 5 Aug', 'auto', 'Foreclosure quotes', 'Retail Lending', 'resolved', 'AI', null, 0.92, 'Quote sent 04:30', 'retail', c('Vinod Shetty', 'vinod.shetty@gmail.com', 'CIF 2201887', 'PL ••2201'), 460],
  [48178, 'P4', 'Standing instruction for monthly utility debit', 'auto', 'Account maintenance', 'Retail Service Desk', 'resolved', 'D. Kulkarni', null, 0.84, 'Registered 03:55', 'retail', c('Pooja Hegde', 'pooja.hegde@gmail.com', 'CIF 3320556', 'A/C ••0556'), 500],
];

const BODY: Record<number, string> = {
  48216: 'Please send me the interest certificate for my savings account for FY 2025-26. I need it for my tax filing this week.',
  48215: 'I see a debit of ₹590 described as an annual card fee on my statement. I was told the card was lifetime free. Why was this charged?',
  48214: 'We sent USD 42,000 to our supplier in Hamburg on 21 July (our reference INV-7781). The beneficiary says it has not arrived. Please trace the payment and tell us where it is.',
  48213: 'Kindly issue a new cheque book of 50 leaves for my savings account. I will collect it from the same branch.',
  48212: 'A NEFT of ₹1,20,000 to Shree Traders was sent twice from our account this morning. Please reverse the duplicate before the cut-off.',
  48209: 'I have changed my mobile number. I completed the OTP verification on the new number through net banking. Please update the registered mobile.',
  48206: 'What is the status of my chargeback DSP-11009 for card ending 2214? It has been three weeks.',
  48204: 'I changed jobs and there will be a gap of about six months before my salary resumes at the new employer. Can my personal loan EMIs be rescheduled?',
  48201: 'I would like to request a waiver of the locker rent for the second year, as the locker was unusable for four months due to branch renovation.',
  48198: 'My mother, who was the joint holder on my account, passed away last month. I need to change the nomination. Please advise what documents are needed.',
  48196: 'What are the current annual maintenance charges for an NRO savings account? Please share the fee schedule.',
  48192: 'I am travelling in Europe and my card was blocked after a transaction in Lisbon. Please put a temporary hold rather than a permanent block.',
  48190: 'I need a balance confirmation letter for my visa application to the UK embassy. Please email it to me.',
  48186: 'I missed my card payment by two days for the first time in four years. Could you please waive the late payment fee?',
  48181: 'Please send me the foreclosure quote for my personal loan ending 2201 as of 5 August.',
  48178: 'Please register a standing instruction to pay my electricity bill every month from my savings account.',
};

export const LIGHT: SeedTicket[] = ROWS.map(
  ([number, priority, subject, lane, queryType, dept, status, owner, dueInMin, confidence, nextMove, board, customer, received]) => ({
    number,
    board,
    lane,
    laneNote:
      lane === 'auto' ? 'Filled in by the AI' : lane === 'draft' ? 'Cited draft in progress' : 'The AI stepped back',
    status,
    priority,
    segment: customer.segment,
    subject,
    customer,
    queryType,
    dept,
    deptLabel: dept === null ? 'Branch Ops' : undefined,
    confidence,
    owner,
    receivedMinAgo: received,
    slaMinutes: priority === 'P1' ? 480 : 1440,
    dueInMin,
    nextMove,
    latencyMs: 1200 + (number % 7) * 150,
    category: lane === 'manual' ? 'Complaints' : lane === 'draft' ? 'Servicing' : 'Transactions',
    subcategory: queryType,
    product: dept === 'Cards' ? 'Credit card' : dept === 'Retail Lending' ? 'Personal loan' : 'Savings account',
    resolvedMinAgo: status === 'resolved' ? Math.max(5, received - 20) : undefined,
    sentiment: number === 48198 ? 'vulnerable' : 'neutral',
    thread: [{ who: customer.name, minAgo: received, body: BODY[number] ?? subject }],
  }),
);

/** Past, closed tickets that make up customer history (and recurrence detection). */
export const HISTORY: { cif: string; number: number; subject: string; daysAgo: number; outcome: string; tone: 'ok' | 'warn' | 'bad'; sameTopic: boolean; queryType: string }[] = [
  { cif: 'CIF 8830412', number: 47120, subject: 'Stop payment on cheque 448902', daysAgo: 39, outcome: 'Resolved in 2h 10m', tone: 'ok', sameTopic: true, queryType: 'Stop payment instruction' },
  { cif: 'CIF 8830412', number: 45877, subject: 'Bulk NEFT file rejected — format error', daysAgo: 86, outcome: 'Resolved in 6h', tone: 'ok', sameTopic: false, queryType: 'Outward remittance' },
  { cif: 'CIF 8830412', number: 44201, subject: 'Stop payment on cheque 447715', daysAgo: 138, outcome: 'Resolved in 3h 40m', tone: 'ok', sameTopic: true, queryType: 'Stop payment instruction' },
  { cif: 'CIF 4410277', number: 46620, subject: 'NRE interest certificate for FY 2024-25', daysAgo: 109, outcome: 'Resolved same day', tone: 'ok', sameTopic: false, queryType: 'Certificate requests' },
  { cif: 'CIF 2290844', number: 48012, subject: 'Unauthorised debit ₹94,500 — first report', daysAgo: 17, outcome: 'Acknowledged, no resolution', tone: 'bad', sameTopic: true, queryType: 'Disputed transactions' },
  { cif: 'CIF 2290844', number: 48088, subject: 'Chasing the dispute — second email', daysAgo: 11, outcome: 'Acknowledged, no resolution', tone: 'bad', sameTopic: true, queryType: 'Disputed transactions' },
  { cif: 'CIF 2290844', number: 43990, subject: 'Card declined abroad', daysAgo: 155, outcome: 'Resolved in 40m', tone: 'ok', sameTopic: false, queryType: 'Fraud reporting' },
  { cif: 'CIF 2290844', number: 41022, subject: 'Statement request', daysAgo: 234, outcome: 'Resolved same day', tone: 'ok', sameTopic: false, queryType: 'Statement re-issue' },
  { cif: 'CIF 6620188', number: 47755, subject: 'Forward cover for USD 300k order', daysAgo: 29, outcome: 'Resolved in 1d 4h', tone: 'warn', sameTopic: true, queryType: 'Trade finance advisory' },
  { cif: 'CIF 3390771', number: 46003, subject: 'EMI bounce — charge reversal', daysAgo: 104, outcome: 'Resolved in 3h', tone: 'ok', sameTopic: false, queryType: 'EMI reschedule requests' },
];
