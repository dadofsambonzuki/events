window.PageEventsDisplay = {
  template: '#page-events-display',
  data() {
    return {
      eventErrorLabel: '',
      event: null,
      ticketTypes: [],
      itemQuantities: {},
      attendeeFields: {},
      copyDetailsToAll: {},
      promoCodeInput: '',
      discountBreakdown: [],
      basketTotal: 0,
      basketItems: [],
      submitting: false,
      checkoutLoading: false,
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
            currency: tt.currency,
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
      for (const item of this.basketItems) {
        if (item.quantity <= 0) continue
        const attendees = this.attendeeFields[item.ticketTypeId] || []
        for (const a of attendees) {
          if (!a.name || !a.email) return false
        }
      }
      return true
    },
    allowEmailNotifications() {
      return Boolean(this.event?.extra?.email_notifications)
    },
    allowNostrNotifications() {
      return Boolean(this.event?.extra?.nostr_notifications)
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
      this.expandAttendeeFor(tt.id)
    },
    expandAttendeeFor(ttId) {
      const item = this.basketItems.find(i => i.ticketTypeId === ttId)
      if (!item) return
      const current = this.attendeeFields[ttId] || []
      while (current.length < item.quantity) {
        current.push({name: '', email: ''})
      }
      while (current.length > item.quantity) {
        current.pop()
      }
      this.attendeeFields[ttId] = current
    },
    updateAttendeeName(ttId, idx, val) {
      if (!this.attendeeFields[ttId]) return
      this.attendeeFields[ttId][idx].name = val
      if (this.copyDetailsToAll[ttId]) {
        for (const a of this.attendeeFields[ttId]) {
          a.name = val
        }
      }
    },
    updateAttendeeEmail(ttId, idx, val) {
      if (!this.attendeeFields[ttId]) return
      this.attendeeFields[ttId][idx].email = val
      if (this.copyDetailsToAll[ttId]) {
        for (const a of this.attendeeFields[ttId]) {
          a.email = val
        }
      }
    },
    onCopyDetails(ttId) {
      if (!this.copyDetailsToAll[ttId]) return
      const attendees = this.attendeeFields[ttId] || []
      if (attendees.length === 0) return
      const first = attendees[0]
      for (let i = 1; i < attendees.length; i++) {
        attendees[i].name = first.name
        attendees[i].email = first.email
      }
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
    nameValidation(val) {
      const regex = /[`!@#$%^&*()_+\-=\[\]{};':"\\|,.<>\/?~]/g
      return !regex.test(val) || this.$t('events.name_validation')
    },
    emailValidation(val) {
      const regex = /^[\w\.-]+@[a-zA-Z\d\.-]+\.[a-zA-Z]{2,}$/
      return regex.test(val) || this.$t('events.email_validation')
    },
    async checkout() {
      if (this.checkoutLoading || !this.canCheckout) return
      this.checkoutLoading = true
      try {
        const items = this.basketItems.map(bi => ({
          ticket_type_id: bi.ticketTypeId,
          quantity: bi.quantity
        }))
        const primaryItem = this.basketItems[0]
        const primaryAttendees =
          this.attendeeFields[primaryItem.ticketTypeId] || []
        const primary = primaryAttendees[0] || {name: '', email: ''}
        const promo_codes = this.promoCodeInput
          .split(',')
          .map(c => c.trim().toUpperCase())
          .filter(c => c.length > 0)
        const refundAddr = (this.event?.extra?.conditional)
          ? ''
          : undefined

        const body = {
          name: primary.name,
          email: primary.email,
          items,
          promo_codes,
          payment_method: null,
          fiat_provider: null,
          nostr_identifier: null,
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
