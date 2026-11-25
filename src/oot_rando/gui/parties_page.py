"""Named-party library page and its integration with the desktop application."""
import json
from datetime import datetime
import os
from pathlib import Path
import secrets
import tempfile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

from oot_rando.parties import PartyLibrary, validate_save_root
from oot_rando.randomizer.artifact import is_randomizer_seed, read_artifact
from oot_rando.randomizer.mods_folder import validate_mod_directory
from oot_rando.gui.generation_worker import generate_isolated
from oot_rando.paths import data_dir
from oot_rando.patching.randomize_kokiri import TITLE


class PartiesPage:
    def init_parties(self):
        self.party_context = None
        self.party_job_paused = False
        self.party_name = tk.StringVar()
        self.party_search = tk.StringVar()
        self.party_library = None
        self.party_library_error = ''
        # Opt-in: off unless the player ticked the box on the My Games page.
        self.parties_enabled = tk.BooleanVar(value=self.app_state.get('parties_enabled') is True)
        if self.parties_enabled.get():
            self.open_party_library()

    def open_party_library(self):
        self.party_library, self.party_library_error = None, ''
        try:
            self.party_library = PartyLibrary()
        except Exception as error:
            self.party_library_error = str(error)

    def parties_on(self):
        return self.parties_enabled.get()

    def toggle_parties(self):
        enabled = self.parties_enabled.get()
        self.write_app_state(parties_enabled=enabled)
        if enabled:
            if self.party_library is None:
                self.open_party_library()
            notice = self.reconcile_longshot_installation()
            if notice:
                self.status.set(notice)
            self.restore_party()
        else:
            # The installed seed stays where it is; it is simply no longer managed.
            self.party_context = None
            if self.slot.get().startswith('party-'):
                self.slot.set('My game')
            if self.artifact:
                self.seed_chip.set('Seed  ' + self.seed_name)
            self.update_home()
        self.sync_parties_page()

    def sync_parties_page(self):
        if not hasattr(self, 'parties_rows'):
            return
        enabled = self.parties_enabled.get()
        for widget in (self.parties_actions, self.parties_search):
            if enabled:
                widget.pack(fill='x', pady=(0, 14), before=self.parties_rows)
            else:
                widget.pack_forget()
        if enabled:
            self.party_name_box.pack(fill='x', pady=(0, 14))
        else:
            self.party_name_box.pack_forget()
        self.render_parties()

    def build_parties(self):
        parent = self.scrollable(self.parties_tab)
        optin = self.card(parent, 'Keep each adventure with its save',
                          'Optional and off by default. When on, every generated seed is kept with its '
                          'own save in a named game that you can switch between. When off, seeds are '
                          'installed directly in the mods folder as before.')
        ttk.Checkbutton(optin, text='Enable My Games', variable=self.parties_enabled,
                        command=self.toggle_parties).pack(anchor='w', pady=(8, 0))
        actions = self.parties_actions = ttk.Frame(parent, style='Page.TFrame')
        ttk.Button(actions, text='+ New party', style='Accent.TButton',
                   command=self.new_party).pack(side='left')
        ttk.Button(actions, text='Import a seed…', command=self.import_party).pack(side='left', padx=8)
        ttk.Button(actions, text='Keep the installed party…',
                   command=self.capture_party).pack(side='left')
        search = self.parties_search = ttk.Frame(parent, style='Page.TFrame')
        ttk.Label(search, text='Search by name', style='Page.TLabel').pack(side='left', padx=(0, 12))
        ttk.Entry(search, textvariable=self.party_search).pack(side='left', fill='x', expand=True)
        self.parties_rows = ttk.Frame(parent, style='Page.TFrame')
        self.parties_rows.pack(fill='both', expand=True)
        self.party_search.trace_add('write', lambda *args: self.render_parties())
        holder = ttk.Frame(self.settings_body, style='Page.TFrame')   # keeps the card at the top
        holder.pack(fill='x')
        box = self.party_name_box = self.card(holder, 'Party name',
                        'The seed and its progress are kept together in My Games.')
        ttk.Entry(box, textvariable=self.party_name).pack(fill='x')
        self.sync_parties_page()

    def parties_ready(self):
        if not self.parties_enabled.get():
            raise ValueError('Enable My Games first.')
        if self.busy:
            self.status.set('An operation is already running.')
            return False
        if self.party_library is None:
            raise ValueError('Library unavailable: ' + self.party_library_error
                             + '\nClose the game, then restart the randomizer to resume the recovery.')
        return True

    def render_parties(self):
        if not hasattr(self, 'parties_rows'):
            return
        for child in self.parties_rows.winfo_children():
            child.destroy()
        if not self.parties_enabled.get():
            return
        if self.party_library is None:
            # Opening may have failed only because the game was still running while an
            # interrupted transaction waited for recovery: try again on every refresh.
            self.open_party_library()
        try:
            if self.party_library is None:
                raise ValueError(self.party_library_error)
            parties = self.party_library.list()
            query = self.party_search.get().strip().casefold()
            shown = [p for p in parties if query in p['name'].casefold()]
            if not shown:
                self.card(self.parties_rows,
                          'No party found' if parties else 'Your next adventure starts here',
                          'Create a party, import a seed or keep the one already installed.'
                          if not parties else 'Try another name.')
            for folder, error in self.party_library.unreadable:
                self.card(self.parties_rows, 'Unreadable party',
                          f'Parties/{folder}/party.json could not be read ({error}). '
                          'The other parties still work; restore that file or move the folder away.')
            for party in shown:
                ident = party['id']
                active = ident == self.party_library.active_id
                bundle = self.party_library.bundle(party)
                manifest = json.loads((bundle / 'seed' / 'manifest.json').read_text(encoding='utf-8'))
                mode = 'Archipelago' if manifest['settings'].get('archipelago') else 'Solo'
                saves = Path(party['saves']) if active else bundle / 'save'
                started = any(saves.glob('slot_*/progress.sav'))
                status = 'ACTIVE' if active else 'In progress' if started else 'Not started yet'
                box = self.card(self.parties_rows)
                heading = self.buttons(box)
                ttk.Label(heading, text=party['name'], style='Heading.TLabel',
                          wraplength=360).pack(side='left', fill='x', expand=True)
                ttk.Label(heading, text=status, style='Chip.TLabel').pack(side='right')
                mods = Path(party['mods'])
                destination = f'Recomp ({mods.parent.name})' if mods.name.lower() == 'mods' else 'Emulator'
                ttk.Label(box, text=f'{mode} · {destination} · '
                          f'Seed {manifest["settings"].get("seed", "")}',
                          style='Muted.TLabel', wraplength=600).pack(anchor='w', pady=(6, 2))
                copied = datetime.fromisoformat(party['updated']).astimezone().strftime('%Y-%m-%d %H:%M')
                ttk.Label(box, text='Last copy: ' + copied,
                          style='Small.TLabel').pack(anchor='w')
                row = self.buttons(box, pady=(12, 0))
                ttk.Button(row, text='Continue' if active else 'Activate', style='Accent.TButton',
                           command=lambda key=ident: self.activate_party(key)).pack(side='left')
                ttk.Button(row, text='Rename', command=lambda key=ident: self.rename_party(key)).pack(side='left', padx=8)
                ttk.Button(row, text='Folder', command=lambda key=ident: self.party_folder(key)).pack(side='left')
                delete = ttk.Button(row, text='Delete', command=lambda key=ident: self.delete_party(key))
                delete.pack(side='right')
                if active:
                    delete.state(['disabled'])
        except Exception as error:
            self.card(self.parties_rows, 'Library unavailable', str(error))

    def new_party(self):
        if self.busy:
            return
        self.party_name.set('')
        self.seed.set(secrets.token_hex(8))
        self.show_page('newseed')

    def party_chip(self):
        name = self.party_context['name']
        return name if len(name) <= 32 else name[:31] + '…'

    def party_destination(self, mods):
        """Bind an exact save account once; never infer it again from timestamps."""
        active = self.party_library.get(self.party_library.active_id) if self.party_library.active_id else None
        if active and Path(active['mods']) == mods:
            return Path(active['saves'])
        initial = Path(self.save_path.get()).parent.parent if self.save_path.get() else None
        chosen = filedialog.askdirectory(parent=self.root,
                    title='Save account folder: the one directly containing slot_00, slot_01…',
                    initialdir=str(initial) if initial and initial.is_dir() else str(mods.parent))
        return validate_save_root(chosen) if chosen else None

    def party_display_name(self, initial=''):
        name = simpledialog.askstring('Party name', 'What would you like to call this party?',
                                     initialvalue=initial, parent=self.root)
        return name.strip() if name and name.strip() else None

    def capture_party(self):
        try:
            if not self.parties_ready():
                return
            if self.party_library.active_id:
                raise ValueError('A party is already managed. It is saved automatically at the next switch.')
            mods = validate_mod_directory(self.mods.get())
            installed = [p for p in mods.iterdir() if is_randomizer_seed(p)] if mods.exists() else []
            if len(installed) != 1:
                raise ValueError('There must be exactly one randomizer seed in the mods folder selected in Setup.')
            name = self.party_display_name(installed[0].name)
            if not name:
                return
            saves = self.party_destination(mods)
            if saves is None:
                return
            manual = self.tracker.manual.path if self.tracker and self.artifact == installed[0] else None
            address = self.ap_address.get()
            self.pause_party_services()
            self.run_job(lambda progress: self.party_library.create(
                name, installed[0], mods, saves, capture=True, tracker=manual, ap_address=address), self.party_opened)
        except Exception as error:
            self.error(error)

    def ensure_party_destination(self, mods):
        if not self.party_library.active_id and mods.exists() and any(is_randomizer_seed(p) for p in mods.iterdir()):
            messagebox.showinfo('Keep your party',
                'First keep the installed party, with its name and its save. '
                'You can then create or import another party.', parent=self.root)
            self.show_page('parties')
            return False
        return True

    def import_party(self):
        try:
            if not self.parties_ready():
                return
            mods = validate_mod_directory(self.mods.get())
            if not self.ensure_party_destination(mods):
                return
            seed = filedialog.askdirectory(title='Seed to import: folder containing manifest.json', parent=self.root)
            if not seed:
                return
            manifest, _ = read_artifact(seed)
            name = self.party_display_name(manifest['settings'].get('seed', ''))
            if not name:
                return
            saves = self.party_destination(mods)
            if saves is None:
                return
            address = self.ap_address.get()
            def job(progress):
                progress('Copying the seed into My Games…')
                return self.party_library.create(name, seed, mods, saves, ap_address=address)
            def done(party):
                self.render_parties()
                self.show_page('parties')
                self.status.set('Party imported. Click Activate to play.')
            self.run_job(job, done)
        except Exception as error:
            self.error(error)

    def pause_party_services(self):
        self.party_job_paused = True
        self.stop_archipelago()
        if self.turbo:
            self.turbo.stop()
        # Stale in-flight results are discarded by poll using tracker identity.
        self.tracker = None

    def activate_party(self, ident):
        try:
            if not self.parties_ready():
                return
            if ident == self.party_library.active_id:
                if not self.party_context or self.party_context['id'] != ident:
                    self.party_opened(self.party_library.get(ident))
                self.show_page('tracker')
                return
            self.party_library._closed()
            address = self.ap_address.get() if self.party_context else None
            self.pause_party_services()
            def job(progress):
                progress('Saving the outgoing party and installing the chosen one…')
                return self.party_library.activate(ident, address)
            self.run_job(job, self.party_opened)
        except Exception as error:
            self.error(error)

    def party_seed_dir(self, party):
        """The installed seed of a party, else its stored snapshot (its port folder vanished)."""
        live_seed = Path(party['mods']) / party['mod_name']
        if live_seed.is_dir():
            return live_seed
        snapshot_seed = self.party_library.bundle(party) / 'seed'
        if snapshot_seed.is_dir():
            return snapshot_seed
        return live_seed

    def party_opened(self, party):
        self.party_job_paused = False
        if not Path(party['mods']).exists():
            # Its port folder vanished (a Longshot update): follow the new build.
            resolved = self.resolve_longshot_mods(party['mods'])
            if resolved:
                try:
                    self.party_library.migrate_mods_directory(resolved, old_mods=party['mods'])
                    party = self.party_library.get(party['id'])
                except (OSError, ValueError) as error:
                    self.error(error)
        self.mods.set(party['mods'])
        self.save_path.set('')
        self.auto_save.set(True)
        self.ap_password.set('')
        self.ap_address.set(party.get('ap_address') or 'archipelago.gg:38281')
        self.slot.set('party-' + party['id'])
        self.use_artifact(self.party_seed_dir(party))
        self.start_turbo()
        self.render_parties()
        self.update_home()
        self.status.set('Active party: ' + party['name'] + '. You can start the game.')

    def match_party(self, directory):
        self.party_context = None
        if self.parties_enabled.get() and self.party_library and self.party_library.active_id:
            party = self.party_library.get(self.party_library.active_id)
            if Path(directory).resolve() == Path(party['mods']) / party['mod_name']:
                self.party_context = party
                self.slot.set('party-' + party['id'])
        if self.party_context is None and self.slot.get().startswith('party-'):
            self.slot.set('My game')

    def restore_party(self):
        if self.parties_enabled.get() and self.party_library and self.party_library.active_id:
            try:
                self.party_opened(self.party_library.get(self.party_library.active_id))
            except Exception as error:
                self.error(error)
            return True
        return False

    def rename_party(self, ident):
        try:
            if not self.parties_ready():
                return
            name = self.party_display_name(self.party_library.get(ident)['name'])
            if name:
                self.party_library.rename(ident, name)
                if self.party_context and self.party_context['id'] == ident:
                    self.party_context = self.party_library.get(ident)
                    self.seed_chip.set(self.party_chip())
                    self.update_home()
                self.render_parties()
        except Exception as error:
            self.error(error)

    def delete_party(self, ident):
        try:
            if not self.parties_ready():
                return
            party = self.party_library.get(ident)
            if messagebox.askyesno('Delete party', f'Remove “{party["name"]}” from My Games?\n\n'
                                  'A copy will remain in Parties/.deleted.', parent=self.root):
                self.party_library.delete(ident)
                self.render_parties()
        except Exception as error:
            self.error(error)

    def party_folder(self, ident):
        try:
            os.startfile(str(self.party_library.folder(ident)))
        except Exception as error:
            self.error(error)

    def generate_party(self, nup, mods, settings, continuing=None):
        if not self.parties_enabled.get():
            return self.generate_plain(nup, mods, settings)
        if not self.parties_ready() or not self.ensure_party_destination(mods):
            return
        initial = continuing['name'] + ' (updated)' if continuing else settings.seed
        name = self.party_display_name(initial) if continuing else (
            self.party_name.get().strip() or self.party_display_name(initial))
        if not name:
            return
        self.party_library._name(name)
        saves = self.party_destination(mods)
        if saves is None:
            return
        self.party_library._closed()
        self.party_name.set(name)
        address = self.ap_address.get()
        outgoing_address = address if self.party_context else None
        previous = dict(self.artifact_manifest) if continuing else None
        manual = self.tracker.manual.path if continuing and self.tracker else None
        self.pause_party_services()
        def job(progress):
            staging = data_dir() / 'party-generation'
            staging.mkdir(parents=True, exist_ok=True)
            # Generate away from installed files: even a generator crash leaves the
            # current seed/save pair intact. Failed activation leaves a selectable party.
            with tempfile.TemporaryDirectory(dir=staging) as temporary:
                temporary = Path(temporary)
                staging_mods = temporary / TITLE
                if continuing:
                    from oot_rando.parties import copy_verified
                    copy_verified(Path(continuing['mods']) / continuing['mod_name'], staging_mods / continuing['mod_name'])
                installed = generate_isolated(nup, staging_mods, settings, temporary / 'archive', progress)
                party = self.party_library.create(name, installed, mods, saves, ap_address=address,
                                                  continue_active=bool(continuing), tracker=manual)
            return self.party_library.activate(party['id'], outgoing_address)
        def done(party):
            self.party_generated(party, continued=bool(continuing))
            if continuing:
                self.offer_save_migration(self.artifact_manifest, previous)
        self.run_job(job, done)

    def generate_plain(self, nup, mods, settings):
        """Install the seed straight into the mods folder (My Games off)."""
        from oot_rando.gui.randomizer_gui import ARCHIVE
        from oot_rando.randomizer.mods_folder import qol_seed_warning
        def job(progress):
            installed = generate_isolated(nup, mods, settings, ARCHIVE, progress)
            return installed, installed
        def done(result):
            directory, installed = result
            previous = self.artifact_manifest
            self.use_artifact(directory)
            self.show_page('tracker')
            self.status.set('Seed generated and installed: ' + str(installed))
            warning = qol_seed_warning(Path(installed).parent)
            messagebox.showinfo('Seed ready', 'Seed installed in:\n' + str(installed)
                                + '\n\nStart a new game to enable automatic tracking and first collections.'
                                + ('\n\n' + warning if warning else ''), parent=self.root)
            self.offer_save_migration(self.artifact_manifest, previous)
        self.run_job(job, done)

    def party_generated(self, party, continued=False):
        self.party_opened(party)
        self.show_page('parties')
        text = ('The save was carried over. The original party stays available.' if continued
                else 'Start a new save in the game.')
        messagebox.showinfo('Party ready', f'“{party["name"]}” is installed.\n\n' + text, parent=self.root)
