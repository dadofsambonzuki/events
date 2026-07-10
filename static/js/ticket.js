;(function() {
  if (window._eventsTicketI18nLoaded) return;
  window._eventsTicketI18nLoaded = true;
  var i18n = window.i18n;
  if (!i18n) return;
  i18n.global.mergeLocaleMessage('en', {
    events: {
      ticket_deactivated: 'Deactivated',
      ticket_paid: 'Paid',
      ticket_not_paid: 'Not Paid',
      checked_in: 'Checked In',
      not_checked_in: 'Not Checked In',
      print: 'Print Ticket',
      copy_url: 'Copy Link',
      ticket_heading: 'Ticket',
      ticket_instructions: 'Bookmark, print or screenshot this page, and present it for registration!',
    }
  });
  i18n.global.mergeLocaleMessage('de', {
    events: {
      ticket_deactivated: 'Deaktiviert',
      ticket_paid: 'Bezahlt',
      ticket_not_paid: 'Nicht bezahlt',
      checked_in: 'Eingecheckt',
      not_checked_in: 'Nicht eingecheckt',
      print: 'Ticket drucken',
      ticket_heading: 'Ticket',
      ticket_instructions: 'Setzen Sie ein Lesezeichen, drucken oder erstellen Sie einen Screenshot dieser Seite und zeigen Sie ihn bei der Registrierung vor!',
    }
  });
  i18n.global.mergeLocaleMessage('es', {
    events: {
      ticket_deactivated: 'Desactivada',
      ticket_paid: 'Pagado',
      ticket_not_paid: 'No pagado',
      checked_in: 'Registrado',
      not_checked_in: 'No registrado',
      print: 'Imprimir entrada',
      ticket_heading: 'Entrada',
      ticket_instructions: '¡Guarda esta página como marcador, imprímela o toma una captura de pantalla y preséntala para registrarte!',
    }
  });
})();
window.PageEventsTicket = {
  template: '#page-events-ticket',
  data() {
    return {
      ticketId: null,
      ticket: null,
      eventName: '',
      ticketTypeName: '',
      printMode: false,
      qrSrc: ''
    }
  },
    methods: {
      copyTicketUrl() {
        const url = `${window.location.origin}/events/ticket/${this.ticketId}`
        navigator.clipboard.writeText(url).then(() => {
          this.$q.notify({message: 'Link copied', type: 'positive'})
        })
      },
    async printWindow() {
      this.printMode = true
      await this.$nextTick()
      await this.waitForPrintAssets()
      setTimeout(() => window.print(), 50)
    },
    async waitForPrintAssets() {
      await this.$nextTick()
      const img = document.querySelector('.ticket-print-qr')
      if (!img) return
      if (img.complete && img.naturalWidth > 0) return
      await new Promise(resolve => {
        const done = () => resolve()
        img.addEventListener('load', done, {once: true})
        img.addEventListener('error', done, {once: true})
        setTimeout(done, 500)
      })
    }
  },
  async created() {
    this.ticketId = this.$route.params.id
    this.qrSrc = `/api/v1/qrcode?data=${encodeURIComponent(
      `ticket://${this.ticketId}`
    )}`
    try {
      const {data} = await LNbits.api.request(
        'GET',
        `/events/api/v1/tickets/${this.ticketId}`
      )
      this.ticket = data
      this.eventName = data.event_name || ''
      this.ticketTypeName = data.ticket_type_name || ''
    } catch (error) {
      LNbits.utils.notifyApiError(error)
    }
    window.addEventListener('afterprint', () => {
      this.printMode = false
    })
  }
}
