window.PageEventsDisplay = {
  template: '#page-events-display',
  data() {
    return {
      eventErrorLabel: '',
      event: null,
      ticketTypes: [],
      ticketTypesLoaded: false,
      itemQuantities: {},
      basketName: '',
      basketEmail: '',
      promoCodeInput: '',
      discountBreakdown: [],
      promoMessages: [],
      basketTotal: 0,
      applyingPromo: false,
      basketItems: [],
      submitting: false,
      checkoutLoading: false,
      anonDialog: false,
      nostrIdentifier: '',
      attendeeFields: {},
      copyDetailsToAll: {},
      ticketLink: {
        show: false,
        data: {
          link: ''
        }
      }
    }
  },
  async mounted() {
    this.eventId = this.$route.params.id
    await this.loadEvent()
    if (this.event) {
      await this.loadTicketTypes()
    }
    this.applyStandaloneChrome()
  },
  computed: {
    formatDescription() {
      return LNbits.utils.convertMarkdown(this.event?.info || '')
    },
    basketCurrency() {
      const c = this.event?.currency || 'sat'
      return c === 'sat' ? 'sats' : c
    },
    basket() {
      return this.basketItems
        .map(item => {
          const tt = this.ticketTypes.find(t => t.id === item.ticketTypeId)
          if (!tt) return null
          return {
            ticketTypeId: tt.id,
            name: tt.name,
            quantity: item.quantity,
            price: tt.price,
            subtotal: tt.price * item.quantity
          }
        })
        .filter(Boolean)
    },
    basketItemCount() {
      return this.basketItems.reduce((n, item) => n + item.quantity, 0)
    },
    basketSummary() {
      const key =
        this.basketItemCount === 1
          ? 'events.basket_summary_one'
          : 'events.basket_summary_many'
      return this.$t(key, {
        count: this.basketItemCount,
        total: `${this.basketTotal} ${this.basketCurrency}`
      })
    },
    needsAnonWarning() {
      return !this.basketName || !this.basketEmail
    },
    canCheckout() {
      if (this.submitting || this.checkoutLoading) return false
      const hasItems = this.basketItems.some(
        item => item.quantity > 0
      )
      if (!hasItems) return false
      if (this.basketEmail && !/^[\w\.-]+@[a-zA-Z\d\.-]+\.[a-zA-Z]{2,}$/.test(this.basketEmail)) return false
      return true
    },
    allowEmailNotifications() {
      return Boolean(this.event?.extra?.email_notifications)
    },
    allowNostrNotifications() {
      return Boolean(this.event?.extra?.nostr_notifications)
    },
    acceptedPaymentMethods() {
      return this.event?.extra?.payment_methods || []
    }
  },
  methods: {
    // Standalone event pages: replace the instance chrome ("Bitfest" -> event name),
    // set the browser title, and cut the link back to the LNbits home. Per-event ids.
    applyStandaloneChrome() {
      const CHROME = {
        '7meNdXUNra5C9AShyAhpCo': {
          label: 'New Music Economy Concert 2026',
          title: 'New Music Economy Concert 2026'
        },
        Bs3V7Q6YPW7V4x2Dfaoype: {
          label: '🟠 Bitfest 2026',
          title: '🟠 Bitfest 2026 · Manchester · 19–22 November'
        }
      }
      let chrome = null
      for (const id of Object.keys(CHROME)) {
        if (location.pathname.includes(id)) {
          chrome = CHROME[id]
          break
        }
      }
      if (!chrome) return
      const set = () => {
        const link = document.querySelector('header .q-toolbar__title a')
        if (!link) return false
        const span =
          link.querySelector('span.q-btn__content span') ||
          link.querySelector('span')
        if (span) span.textContent = chrome.label
        if (link.tagName === 'A') link.removeAttribute('href')
        link.style.pointerEvents = 'none'
        if (document.title !== chrome.title) document.title = chrome.title
        return true
      }
      let tries = 0
      const timer = setInterval(() => {
        if (set() || ++tries > 40) clearInterval(timer)
      }, 150)
    },
    async loadEvent() {
      try {
        const {data} = await LNbits.api.request(
          'GET',
          `/events/api/v1/events/${this.eventId}`
        )
        this.event = data
      } catch (error) {
        this.eventErrorLabel = this.$t('events.event_unavailable')
        LNbits.utils.notifyApiError(error)
      }
    },
    async loadTicketTypes() {
      try {
        const {data} = await LNbits.api.request(
          'GET',
          `/events/api/v1/ticket-types/${this.eventId}`
        )
        const today = new Date().toISOString().slice(0, 10)
        this.ticketTypes = data.filter(
          tt =>
            (tt.extra?.active ?? true) &&
            tt.available_from <= today &&
            tt.available_to >= today &&
            (tt.max_tickets === 0 || tt.sold < tt.max_tickets)
        )
        for (const tt of this.ticketTypes) {
          this.itemQuantities[tt.id] = 0
          this.attendeeFields[tt.id] = []
          this.copyDetailsToAll[tt.id] = false
        }
      } catch (error) {
        LNbits.utils.notifyApiError(error)
      } finally {
        this.ticketTypesLoaded = true
      }
    },
    addToBasket(tt, qty) {
      if (qty <= 0) return
      const existing = this.basketItems.find(
        item => item.ticketTypeId === tt.id
      )
      if (existing) {
        existing.quantity = qty
      } else {
        this.basketItems.push({ticketTypeId: tt.id, quantity: qty})
      }
      this.itemQuantities[tt.id] = qty
      this.rebuildBasket()
      this.$q.notify({
        message: this.$t('events.ticket_added_to_basket', {
          qty,
          name: tt.name
        }),
        type: 'positive',
        position: 'top',
        timeout: 2000
      })
    },
    removeFromBasket(ttId) {
      const removed = this.basket.find(item => item.ticketTypeId === ttId)
      this.basketItems = this.basketItems.filter(
        item => item.ticketTypeId !== ttId
      )
      this.itemQuantities[ttId] = 0
      this.attendeeFields[ttId] = []
      this.rebuildBasket()
      if (removed) {
        this.$q.notify({
          message: this.$t('events.ticket_removed_from_basket', {
            name: removed.name
          }),
          type: 'info',
          position: 'top',
          timeout: 2000
        })
      }
    },
    addToBasketBtn(tt) {
      const qty = this.itemQuantities[tt.id] || 0
      if (qty <= 0) return
      this.addToBasket(tt, qty)
    },
    rebuildBasket() {
      this.basketItems = this.basketItems.filter(
        item => item.quantity > 0
      )
      if (this.basketItems.length === 0) {
        this.discountBreakdown = []
        this.promoMessages = []
        this.basketTotal = 0
        return
      }
      let subtotal = 0
      for (const bi of this.basketItems) {
        const tt = this.ticketTypes.find(t => t.id === bi.ticketTypeId)
        if (tt) {
          subtotal += tt.price * bi.quantity
        }
      }
      this.basketTotal = subtotal
      this.discountBreakdown = []
      this.promoMessages = []
    },
    async applyPromoCode() {
      // Offer codes are matched case-insensitively by the server, but TICKET
      // IDs are case-sensitive primary keys — never upper-case what the buyer
      // typed or a valid ticket ID stops resolving.
      const codes = this.promoCodeInput
        .split(',')
        .map(c => c.trim())
        .filter(c => c.length > 0)
      if (!codes.length) {
        this.rebuildBasket()
        return
      }
      if (!this.basketItems.length) return
      this.applyingPromo = true
      try {
        const items = this.basketItems.map(bi => ({
          ticket_type_id: bi.ticketTypeId,
          quantity: bi.quantity
        }))
        const {data} = await LNbits.api.request(
          'POST',
          `/events/api/v1/promo/validate/${this.eventId}`,
          null,
          {codes, items}
        )
        this.basketTotal = data.total
        this.discountBreakdown = (data.discounts_applied || []).map(d => ({
          label: `${d.code}: ${d.amount_saved} ${this.basketCurrency}`
        }))
        // An offer that needs a ticket ID reports what it still wants, so the
        // buyer is told to add one instead of seeing nothing happen.
        this.promoMessages = (data.pending || []).map(p => ({
          kind: 'pending',
          label: this.$t(
            // A codeless offer has no code to name in the sentence.
            p.code
              ? 'events.promo_pending_ticket'
              : 'events.promo_pending_ticket_id_only',
            {code: p.code}
          )
        }))
        // A token that could not be used says why, instead of silently doing
        // nothing (which is indistinguishable from a mistyped code).
        this.promoMessages = this.promoMessages.concat(
          (data.rejections || []).map(r => ({
            kind: 'rejected',
            label: `${r.code}: ${this.promoReasonLabel(r.reason, r.code)}`
          }))
        )
        if (
          !this.discountBreakdown.length &&
          !this.promoMessages.length &&
          codes.length
        ) {
          this.promoMessages = [
            {kind: 'rejected', label: this.$t('events.promo_none_found')}
          ]
        }
      } catch (error) {
        LNbits.utils.notifyApiError(error)
        this.rebuildBasket()
      } finally {
        this.applyingPromo = false
      }
    },
    promoReasonLabel(reason, token) {
      // A token that looks like a ticket id gets the actionable message: the
      // usual cause is an incomplete copy, not a wrong code. Accept the pasted
      // link forms too, because that is what the ticket page hands out.
      const bare = String(token || '')
        .replace(/^ticket:\/\//, '')
        .replace(/^.*\/ticket\//, '')
        .trim()
      if (reason === 'ticket_not_found' && /^[A-Za-z0-9_-]{16,80}$/.test(bare)) {
        return this.$t('events.promo_reason_ticket_id_not_found')
      }
      const key = `events.promo_reason_${reason}`
      const label = this.$t(key)
      // $t returns the key itself when it is missing; fall back to a generic
      // message rather than showing a raw i18n key to a buyer.
      return label === key ? this.$t('events.promo_reason_generic') : label
    },
  
    scrollToBasket() {
      const card = this.$refs.basketCard
      const el = card && card.$el ? card.$el : card
      if (el && el.scrollIntoView) {
        el.scrollIntoView({behavior: 'smooth', block: 'start'})
      }
    },
    async checkout() {
      if (this.checkoutLoading || !this.canCheckout) return
      // An anonymous ticket (no name and/or no email) can only be reached again
      // through the ticket link we hand back, so acknowledge that first.
      if (this.needsAnonWarning) {
        this.anonDialog = true
        return
      }
      await this.submitCheckout()
    },
    async submitCheckout() {
      this.checkoutLoading = true
      try {
        const items = this.basketItems.map(bi => ({
          ticket_type_id: bi.ticketTypeId,
          quantity: bi.quantity
        }))
        // Same as applyPromoCode: codes are case-insensitive server-side but
        // ticket IDs are not, so send what the buyer typed.
        const promo_codes = this.promoCodeInput
          .split(',')
          .map(c => c.trim())
          .filter(c => c.length > 0)
        const refundAddr = (this.event?.extra?.conditional)
          ? ''
          : undefined

        const body = {
          name: this.basketName,
          email: this.basketEmail || null,
          items,
          promo_codes,
          payment_method: null,
          fiat_provider: this.event?.extra?.fiat_provider || null,
          nostr_identifier: this.nostrIdentifier || null,
          refund_address: refundAddr || null
        }

        const {data} = await LNbits.api.request(
          'POST',
          `/events/api/v1/baskets/${this.eventId}`,
          null,
          body
        )

        const firstTicket = data.tickets?.[0]
        if (data.basket?.id) {
          this.$router.push(`/events/basket/${data.basket.id}`)
        } else if (firstTicket) {
          this.ticketLink.show = true
          this.ticketLink.data.link = `/events/ticket/${firstTicket.id}`
        }
      } catch (error) {
        LNbits.utils.notifyApiError(error)
      } finally {
        this.checkoutLoading = false
      }
    },
    confirmAnonCheckout() {
      this.anonDialog = false
      this.submitCheckout()
    }
  }
}
