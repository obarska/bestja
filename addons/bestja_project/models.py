# -*- coding: utf-8 -*-

from openerp import models, fields, api, exceptions


class Project(models.Model):
    _name = 'bestja.project'
    _inherit = ['message_template.mixin']

    def current_members(self):
        """
        Limit to members of the current organization only.
        """
        try:
            # try to use organization configured in the current project
            project = self.browse([self.env.context['params']['id']])
            organization = project.organization
        except KeyError:
            # most likely a new project, use organization the user coordinates
            organization = self.env.user.coordinated_org
        return [
            '|',  # noqa odoo-domain indent
                ('id', 'in', organization.volunteers.ids),
                ('coordinated_org', '=', organization.id),
            ]

    name = fields.Char(required=True, string="Nazwa")
    organization = fields.Many2one(
        'organization',
        default=api.model(lambda self: self.env.user.coordinated_org),
        required=True,
        string="Organizacja",
        domain=lambda self: [('coordinator', '=', self.env.user.id)],
    )
    manager = fields.Many2one(
        'res.users',
        domain=current_members,
        string="Menadżer projektu",
    )
    responsible_user = fields.Many2one(
        'res.users',
        string="Osoba odpowiedzialna",
        compute='_responsible_user'
    )
    date_start = fields.Datetime(
        required=True,
        string="od dnia",
    )
    date_stop = fields.Datetime(
        required=True,
        string="do dnia",
    )
    members = fields.Many2many(
        'res.users',
        relation='project_members_rel',
        column1='project',
        column2='member',
        domain=current_members,
        string="Zespół"
    )
    tasks = fields.One2many('bestja.task', 'project', string="Zadania")
    tasks_count = fields.Integer(compute='_tasks_count', string="Liczba zadań")
    done_tasks_count = fields.Integer(compute='_tasks_count', string="Liczba skończonych zadań")

    @api.one
    @api.depends('manager', 'organization.coordinator')
    def _responsible_user(self):
        if self.manager:
            self.responsible_user = self.manager
        else:
            self.responsible_user = self.organization.coordinator

    @api.one
    @api.depends('tasks')
    def _tasks_count(self):
        self.tasks_count = len(self.tasks)
        self.done_tasks_count = self.tasks.search_count([
            ('project', '=', self.id),
            ('state', '=', 'done')
        ])

    @api.model
    def create(self, vals):
        record = super(Project, self).create(vals)
        record.send(
            template='bestja_project.msg_manager',
            recipients=record.manager,
        )
        record.manager.sync_manager_groups()
        return record

    @api.multi
    def write(self, vals):
        old_manager = None
        if 'manager' in vals:
            # Manager changed. Keep the old one.
            old_manager = self.manager
        val = super(Project, self).write(vals)
        if old_manager:
            self.send(
                template='bestja_project.msg_manager',
                recipients=self.manager,
            )
            self.send(
                template='bestja_project.msg_manager_changed',
                recipients=old_manager,
            )
            self.manager.sync_manager_groups()
            old_manager.sync_manager_groups()
        return val

    @api.one
    @api.constrains('date_start', 'date_stop')
    def _check_project_dates(self):
        """
            Date of the beginning of the project needs to be
            before the end
        """
        if (self.date_start > self.date_stop):
            raise exceptions.ValidationError("Data rozpoczęcia projektu musi być przed datą zakończenia.")


class Task(models.Model):
    _name = 'bestja.task'
    _inherit = ['message_template.mixin']
    _order = 'state desc'
    STATES = [
        ('new', "nowe"),
        ('in_progress', "w trakcie realizacji"),
        ('done', "zrealizowane"),
    ]

    def current_project_members(self):
        """
        Returns a domain selecting members of the current project.
        """
        active_id = self.env.context.get('active_id')
        if active_id is None:
            return []
        project = self.env['bestja.project'].browse([active_id])
        return [('id', 'in', project.members.ids)]

    name = fields.Char(required=True, string="Nazwa zadania")
    state = fields.Selection(STATES, default='new', string="Status")
    user = fields.Many2one(
        'res.users',
        domain=current_project_members,
        string="Wykonawca zadania",
    )
    user_assigned_task = fields.Boolean(
        compute='_user_assigned_task'
    )
    date_start = fields.Datetime(required=True, string="od dnia")
    date_stop = fields.Datetime(required=True, string="do dnia")
    date_button_click_start = fields.Datetime(string="data rozpoczęcia")
    date_button_click_stop = fields.Datetime(string="data zakończenia")
    description = fields.Text(string="Opis zadania")
    project = fields.Many2one(
        'bestja.project',
        required=True,
        ondelete='cascade',
        string="Projekt",
    )

    @api.one
    def _user_assigned_task(self):
        """
        Checks if current user == user responsible for task,
        for hiding and unhiding button "rozpocznij"
        """
        self.user_assigned_task = (self.env.user.id == self.user.id)

    @api.one
    def set_in_progress(self):
        self.state = 'in_progress'
        self.date_button_click_start = fields.Datetime.now()

    @api.one
    def set_done(self):
        self.state = 'done'
        self.date_button_click_stop = fields.Datetime.now()
        self.send(
            template='bestja_project.msg_task_done_user',
            recipients=self.user,
        )
        self.send(
            template='bestja_project.msg_task_done_manager',
            recipients=self.project.responsible_user,
        )

    @api.model
    def create(self, vals):
        record = super(Task, self).create(vals)
        record.send(
            template='bestja_project.msg_task',
            recipients=record.user,
        )
        return record

    @api.multi
    def write(self, vals):
        old_user = None
        if 'user' in vals:
            old_user = self.user
        val = super(Task, self).write(vals)
        if old_user:
            self.send(
                template='bestja_project.msg_task',
                recipients=self.user,
            )
            self.send(
                template='bestja_project.msg_task_changed',
                recipients=old_user,
                sender=self.env.user,
            )
        return val

    @api.one
    @api.constrains('date_start', 'date_stop')
    def _check_task_dates(self):
        """
            Date of the beginning of the task needs to be
            before the end and should be within project dates.
        """
        if (self.date_start > self.date_stop):
            raise exceptions.ValidationError("Data rozpoczęcia zadania musi być przed datą zakończenia.")
        if (self.project.date_start > self.date_start or self.project.date_stop < self.date_stop):
            raise exceptions.ValidationError("Zadanie musi odbywać się podczas trwania projektu.")


class UserWithProjects(models.Model):
    _inherit = 'res.users'

    projects = fields.Many2many(
        'bestja.project',
        relation='project_members_rel',
        column1='member',
        column2='project',
        string="Projekty"
    )

    managed_projects = fields.One2many(
        'bestja.project',
        inverse_name='manager'
    )

    @api.one
    def sync_manager_groups(self):
        """
        Add / remove user from the managers group, based on whether
        she manages a project.
        """
        self.sync_group(
            group=self.env.ref('bestja_project.managers'),
            domain=[('managed_projects', '!=', False)],
        )
