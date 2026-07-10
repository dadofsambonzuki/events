<template id="page-events-ticket">
  <div class="row q-col-gutter-md justify-center">
    <div class="col-12 col-md-7 col-lg-6 q-gutter-y-md">
      <q-card class="q-pa-lg">
        <q-card-section class="q-pa-none text-center">
          <h3 class="q-my-sm" v-text="eventName"></h3>
          <h5 v-if="ticketTypeName" class="q-my-sm" v-text="ticketTypeName"></h5>
          <div v-if="ticket && ticket.extra?.deactivated" class="q-mb-md">
            <q-badge
              color="negative"
              class="q-pa-sm"
              v-text="$t('events.ticket_deactivated')"
            ></q-badge>
          </div>
          <lnbits-qrcode
            :value="`ticket://${ticketId}`"
            :options="{width: 500}"
            :show-buttons="false"
            :nfc="false"
          ></lnbits-qrcode>
          <div class="q-mt-md">
            <q-btn unelevated color="positive" @click="copyTicketUrl">
              <span v-text="$t('events.copy_url')"></span>
            </q-btn>
            <q-btn unelevated color="positive" @click="printWindow" class="q-ml-sm">
              <span v-text="$t('events.print')"></span>
            </q-btn>
          </div>
        </q-card-section>
      </q-card>
    </div>
  </div>

  <Teleport to="body">
    <div class="ticket-print-sheet" v-if="printMode">
      <h3 class="ticket-print-event" v-text="eventName"></h3>
      <h5 v-if="ticketTypeName" class="ticket-print-type" v-text="ticketTypeName"></h5>
      <img class="ticket-print-qr" :src="qrSrc" alt="Ticket QR" v-if="qrSrc" />
    </div>
  </Teleport>
</template>

<style>
@media print {
  @page {
    size: auto;
    margin: 0;
  }

  html {
    font-size: 12px !important;
  }

  * {
    color: black !important;
    background: white !important;
    box-shadow: none !important;
  }

  body > * {
    display: none !important;
  }

  .ticket-print-sheet {
    display: flex !important;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    width: 100vw;
    min-height: 100vh;
    margin: 0 !important;
    padding: 0 !important;
    background: white !important;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }

  .ticket-print-event {
    text-align: center;
    margin-bottom: 8px;
  }

  .ticket-print-type {
    text-align: center;
    margin-top: 0;
    margin-bottom: 20px;
    color: #666 !important;
  }

  .ticket-print-qr {
    display: block;
    width: 320px;
    height: 320px;
    object-fit: contain;
  }
}
</style>
