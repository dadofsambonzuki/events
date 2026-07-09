window.PageEventsDisplay = {
  template: '#page-events-display',
  data() {
    return {
      eventErrorLabel: '',
      event: null,
      ticketTypes: [],
      itemQuantities: {},
      basketName: '',
      basketEmail: '',
      promoCodeInput: '',
      discountBreakdown: [],
      basketTotal: 0,
      applyingPromo: false,
      basketItems: [],
      submitting: false,
      checkoutLoading: false,
      nostrIdentifier: '',
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
      const pm = this.event?.extra?.payment_methods || []
      if (!pm.length && this.event?.allow_fiat) {
        return ['ln', 'fiat']
      }
      return pm
    }
  },
  methods: {
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
    },
    removeFromBasket(ttId) {
      this.basketItems = this.basketItems.filter(
        item => item.ticketTypeId !== ttId
      )
      this.itemQuantities[ttId] = 0
      this.attendeeFields[ttId] = []
      this.rebuildBasket()
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
    },
    async applyPromoCode() {
      const codes = this.promoCodeInput
        .split(',')
        .map(c => c.trim().toUpperCase())
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
        if (!data.discounts_applied?.length && codes.length) {
          this.discountBreakdown = [{label: 'No valid promo codes found'}]
        }
      } catch (error) {
        LNbits.utils.notifyApiError(error)
        this.rebuildBasket()
      } finally {
        this.applyingPromo = false
      }
    },
  
    async checkout() {
      if (this.checkoutLoading || !this.canCheckout) return
      this.checkoutLoading = true
      try {
        const items = this.basketItems.map(bi => ({
          ticket_type_id: bi.ticketTypeId,
          quantity: bi.quantity
        }))
        const promo_codes = this.promoCodeInput
          .split(',')
          .map(c => c.trim().toUpperCase())
          .filter(c => c.length > 0)
        const refundAddr = (this.event?.extra?.conditional)
          ? ''
          : undefined

        const body = {
          name: this.basketName,
          email: this.basketEmail,
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

        if (data.payment_request?.satspay_charge_url) {
          window.location.href = data.payment_request.satspay_charge_url
          return
        }

        const firstTicket = data.tickets?.[0]
        if (data.basket && data.totals?.total === 0 && firstTicket) {
          this.ticketLink.show = true
          this.ticketLink.data.link = `/events/ticket/${firstTicket.id}`
        } else if (firstTicket) {
          this.ticketLink.show = true
          this.ticketLink.data.link = `/events/ticket/${firstTicket.id}`
        }
      } catch (error) {
        LNbits.utils.notifyApiError(error)
      } finally {
        this.checkoutLoading = false
      }
    }
  }
}
